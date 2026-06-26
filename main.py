import os
import logging
import json
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
from dotenv import load_dotenv
from pydantic import ValidationError

from models import TicketRequest, TicketResponse, EvidenceVerdict, CaseType, Department, Severity
from prompt import SYSTEM_PROMPT

load_dotenv()

app = FastAPI(title="QueueStorm Investigator Copilot")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize client
api_key = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(
    api_key=api_key,       
    base_url="https://api.groq.com/openai/v1"  
)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/analyze-ticket", response_model=TicketResponse)
async def analyze_ticket(request: TicketRequest):
    # 1. Prepare data
    formatted_history = json.dumps([tx.model_dump() for tx in request.transaction_history], indent=2)
    campaign_str = request.campaign_context if request.campaign_context else "None Provided"
    metadata_str = json.dumps(request.metadata) if request.metadata else "None Provided"

    user_payload = f"""
    Ticket ID: {request.ticket_id}
    Complaint: {request.complaint}
    Language: {request.language or "Unknown"}
    Channel: {request.channel or "Unknown"}
    User Type: {request.user_type or "Unknown"}
    Campaign Context: {campaign_str}
    Metadata: {metadata_str}
    
    Transaction History:
    {formatted_history}
    """

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_payload}
    ]

    # 2. Automated Self-Healing Retry Loop
    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            if not api_key:
                raise ValueError("OPENAI_API_KEY is missing from environment.")

            completion = await client.chat.completions.create(
                model="llama-3.3-70b-versatile", 
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0
            )
            
            raw_json_str = completion.choices[0].message.content.strip()
            if raw_json_str.startswith("```"):
                raw_json_str = raw_json_str.strip("`").replace("json\n", "", 1)
            
            raw_json_dict = json.loads(raw_json_str)
            ai_response = TicketResponse(**raw_json_dict)
            
            # 3. Post-processing Guardrails
            customer_reply_lower = ai_response.customer_reply.lower()
            
            # Credential theft filter
            for bad_word in ["pin", "otp", "password", "credential"]:
                if bad_word in customer_reply_lower:
                    ai_response.customer_reply = "We have received your ticket. Please remember that we will never ask for your PIN, OTP, or password. Our team is investigating."
                    ai_response.severity = Severity.critical
                    ai_response.human_review_required = True
            
            # Unauthorized refund promise filter
            if "will refund" in customer_reply_lower or "will reverse" in customer_reply_lower:
                ai_response.customer_reply = "We have flagged this transaction for review. Any eligible amount will be returned through official channels."
                ai_response.human_review_required = True
                
            # Rule-based human review escalation overrides
            if ai_response.evidence_verdict == EvidenceVerdict.inconsistent or ai_response.case_type == CaseType.phishing_or_social_engineering:
                ai_response.human_review_required = True

            return ai_response

        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"Attempt {attempt + 1} failed validation: {str(e)}")
            if attempt < max_attempts - 1:
                messages.append({"role": "assistant", "content": raw_json_str if 'raw_json_str' in locals() else ""})
                messages.append({"role": "user", "content": f"Previous JSON parsing error: {str(e)}. Correct it."})
            else:
                return TicketResponse(
                    ticket_id=request.ticket_id,
                    relevant_transaction_id=None,
                    evidence_verdict=EvidenceVerdict.insufficient_data,
                    case_type=CaseType.other,
                    severity=Severity.medium,
                    department=Department.customer_support,
                    agent_summary="Automated parse error occurred. Ticket shifted to manual routing.",
                    recommended_next_action="Review original log data manually.",
                    customer_reply="We have received your inquiry and our support team is investigating.",
                    human_review_required=True,
                    confidence=0.5,
                    reason_codes=["system_fallback_active"]
                )
        except Exception as e:
            # This catches the "Internal engine fault" but now tells us WHY
            logger.error(f"Unrecoverable error processing ticket {request.ticket_id}: {str(e)}")
            return JSONResponse(status_code=500, content={"message": f"DEBUG_ERROR: {str(e)}"})