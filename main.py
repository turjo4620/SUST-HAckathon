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

# Load environment variables
load_dotenv()

app = FastAPI(title="QueueStorm Investigator Copilot")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize client using the AsyncOpenAI library pointing to Groq's endpoint
api_key = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(
    api_key=api_key,       
    base_url="https://api.groq.com/openai/v1"  
)

@app.get("/health")
async def health_check():
    """Judge harness calls this to confirm service readiness."""
    return {"status": "ok"}

@app.post("/analyze-ticket", response_model=TicketResponse)
async def analyze_ticket(request: TicketRequest):
    # 1. Serialize transaction history to clean JSON text for the model
    formatted_history = json.dumps([tx.model_dump() for tx in request.transaction_history], indent=2)
    campaign_str = request.campaign_context if request.campaign_context else "None Provided"
    metadata_str = json.dumps(request.metadata) if request.metadata else "None Provided"

    user_payload = f"""
    Ticket ID: {request.ticket_id}
    Complaint: {request.complaint}
    Language: {request.language if request.language else 'en'}
    Channel: {request.channel if request.channel else 'unknown'}
    User Type: {request.user_type if request.user_type else 'customer'}
    Campaign Context: {campaign_str}
    Metadata Context: {metadata_str}
    
    Transaction History Records:
    {formatted_history}
    """

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_payload}
    ]

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            # 2. Call the AI model asynchronously
            response = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.0,  # Strict reasoning focus
                response_format={"type": "json_object"}
            )
            
            raw_json_str = response.choices[0].message.content
            logger.info(f"Raw AI response iteration {attempt + 1}: {raw_json_str}")
            
            # 3. Clean string responses if wrapped in markdown formatting blocks
            if raw_json_str.startswith("```json"):
                raw_json_str = raw_json_str.split("```json")[1].split("```")[0].strip()
            elif raw_json_str.startswith("```"):
                raw_json_str = raw_json_str.split("```")[1].split("```")[0].strip()
            
            parsed_data = json.loads(raw_json_str.strip())
            
            # Ensure the model didn't accidentally alter incoming request token id
            parsed_data["ticket_id"] = request.ticket_id

            # 4. Strictly validate parsed output with Pydantic contract
            ai_response = TicketResponse(**parsed_data)

            # 5. Programmatic Hard Guardrails Firewall
            customer_reply_lower = ai_response.customer_reply.lower()
            
            # Guardrail 1: Prohibits leaking internal credentials or checking for security credentials
            for bad_word in ["pin", "otp", "password", "credential"]:
                if bad_word in customer_reply_lower:
                    ai_response.customer_reply = "We have received your ticket. Please remember that we will never ask for your PIN, OTP, or password. Our team is investigating."
                    ai_response.severity = Severity.critical
                    ai_response.human_review_required = True
            
            # Guardrail 2: Resets premature refund confirmations to standard compliance phrasing
            if "will refund" in customer_reply_lower or "will reverse" in customer_reply_lower:
                ai_response.customer_reply = "We have flagged this transaction for review. Any eligible amount will be returned through official channels."
                ai_response.human_review_required = True
                
            # Guardrail 3: Hard rules forcing downstream human reviews
            if ai_response.evidence_verdict == EvidenceVerdict.inconsistent or ai_response.case_type == CaseType.phishing_or_social_engineering:
                ai_response.human_review_required = True

            # Return well-formed Pydantic object
            return ai_response

        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"Attempt {attempt + 1} failed validation: {str(e)}")
            if attempt < max_attempts - 1:
                messages.append({"role": "assistant", "content": raw_json_str if 'raw_json_str' in locals() else ""})
                messages.append({"role": "user", "content": f"Previous JSON validation parsing error: {str(e)}. Please correct your keys, structures, and enum choices according to the schema rules exactly."})
            else:
                # Exhausted all structural self-healing retry strategies. Run robust static default schema object fallback.
                return TicketResponse(
                    ticket_id=request.ticket_id,
                    relevant_transaction_id=None,
                    evidence_verdict=EvidenceVerdict.insufficient_data,
                    case_type=CaseType.other,
                    severity=Severity.medium,
                    department=Department.customer_support,
                    agent_summary="Automated structural parsing error occurred. Ticket shifted to manual routing fallback.",
                    recommended_next_action="Review original log parameters manually.",
                    customer_reply="We have received your inquiry and our support team is investigating.",
                    human_review_required=True,
                    confidence=0.5,
                    reason_codes=["system_fallback_active"]
                )
        except Exception as e:
            logger.error(f"Unrecoverable runtime error processing ticket {request.ticket_id}: {str(e)}")
            return JSONResponse(
                status_code=500, 
                content={"message": f"An internal system framework error occurred: {str(e)}"}
            )