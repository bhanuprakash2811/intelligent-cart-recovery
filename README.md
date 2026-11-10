# YottaFlex Cart Recovery MVP

A locally runnable synthetic e-commerce abandoned-cart recovery and attribution platform.

## Stack
Python, PostgreSQL 16 (Docker), SQLAlchemy, FastAPI, Streamlit.

## Windows setup
1. Install Python 3.11+ and Docker Desktop. Start Docker Desktop.
2. In PowerShell, extract this repository and `cd` into its folder.
3. Create and activate a virtual environment:
   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and activate again.
4. Install dependencies:
   ```powershell
   python -m pip install -r requirements.txt
   ```
5. Start PostgreSQL:
   ```powershell
   docker compose up -d
   docker compose ps
   ```
6. Generate a small smoke-test dataset:
   ```powershell
   python -m app.generate --small
   ```
7. Generate the challenge-scale dataset:
   ```powershell
   python -m app.generate
   ```
   This resets the project tables before generating the data. Do not run it against a database containing data you need to preserve.
8. Start the API:
   ```powershell
   uvicorn app.main:app --reload
   ```
   Open http://127.0.0.1:8000/docs
9. In a second terminal, start the dashboard:
   ```powershell
   streamlit run dashboard/app.py
   ```
10. Run validation:
   ```powershell
   python -m app.validate
   ```

## Dataset defaults
- 20,000 synthetic customers
- 50,000 carts
- Exactly 20,000 carts labelled abandoned by the generator
- 10,000 send attempts
- Two consent-channel records per customer
- 4 campaigns and 4 templates

The generator uses a fixed seed (`20261107`). Set environment variables before running to change volumes. All data is synthetic and uses `.test` email domains.

## Important implementation notes and limitations
- This is an MVP starter, not a production marketing platform.
- Message delivery is simulated; no email/SMS provider is contacted.
- Consent is modeled as a channel-specific synthetic state at signup. A richer version should store opt-in and opt-out events as immutable history and evaluate the latest event at send time.
- The current campaign generator chooses one channel per send and records suppressed attempts.
- Abandonment labels are generated deterministically as part of the synthetic scenario. The validation engine and business engine should be extended to independently recompute them from timestamps and status.
- Attribution is generated only for a subset of clicked sends; the 72-hour window is represented by generated timestamps. Add explicit query-based attribution reconciliation before claiming production readiness.
- Run the 100-cart smoke test first. Inspect row counts and validation output before generating the full dataset.

## AI use disclosure
Maintain `ai_usage/prompts.md` with every prompt sent to each AI tool used, as required by the challenge. Review, understand, and test all generated code. Do not claim AI-assisted code was written without assistance.
