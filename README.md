# QueueStorm Investigator

## Architecture
- **API Framework**: FastAPI with Pydantic for strict schema enforcement.
- **AI Engine**: LLaMa-3.3-70b (via Groq API) for reasoning.
- **Safety**: Programmatic middleware firewall that intercepts unsafe outputs (PIN/OTP requests, unauthorized refund promises) and forces safe, company-approved language.

## Key Features
- **Evidence-Based Reasoning**: The system cross-references transaction history rather than just classifying text.
- **Self-Healing**: Implements a retry loop for automated parsing errors.
- [cite_start]**Fintech Compliance**: Adheres to all safety rules provided in the Problem Statement[cite: 94].

## Setup
1. Create a `.env` file with `OPENAI_API_KEY=your_groq_key`.
2. Run `pip install -r requirements.txt`.
3. Start the server: `uvicorn main:app --host 0.0.0.0 --port 10000`.

## Known Limitations
- The system prioritizes safety over confidence. It defaults to 'human_review_required: true' whenever there is ambiguity, ensuring 0% risk of unauthorized financial action.