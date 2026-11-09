# Document Analysis Chatbot

An AI-powered chatbot that lets you upload **PowerPoint presentations** (`.pptx`) and **Excel spreadsheets** (`.xlsx`), then ask natural-language questions to get data-driven insights from the documents.

---

## Features

- 📊 **Multi-format support** – Upload `.pptx` and `.xlsx` files (multiple files at once)
- 🤖 **RAG pipeline** – Retrieval-Augmented Generation using OpenAI embeddings + FAISS vector search
- 💬 **Conversational UI** – Clean chat interface with source attribution
- ⚡ **Incremental uploads** – Add more documents without losing existing context
- 🔄 **Reset** – Clear all documents and start fresh at any time

---

## Architecture

```
static/index.html       ← Browser frontend (HTML + vanilla JS)
app/
  main.py               ← FastAPI backend (REST API)
  parsers.py            ← PowerPoint & Excel text extraction
  rag.py                ← LangChain RAG pipeline (FAISS + OpenAI)
run.py                  ← Startup script
```

---

## Prerequisites

- Python 3.10+
- An **OpenAI API key** (uses `gpt-4o-mini` and `text-embedding-ada-002`)

---

## Setup & Run

```bash
# 1. Clone the repo
git clone https://github.com/karanpl23/chatbot.git
cd chatbot

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure your API key
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...

# 5. Start the server
python run.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Usage

1. **Upload** – Drag and drop (or click to browse) your `.pptx` or `.xlsx` files and click **Upload & Analyse**.
2. **Chat** – Type a question in the chat box and press **Enter** or the send button.
3. **Reset** – Click **Clear All Documents** to start over.

### Example questions

- *"What were the key revenue figures mentioned in the presentation?"*
- *"Summarize the main findings from slide 3."*
- *"Which product category had the highest sales in Q4?"*
- *"What trends do you see in the financial data?"*

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serve the frontend |
| `POST` | `/upload` | Upload `.pptx`/`.xlsx` files |
| `POST` | `/chat` | Ask a question (`{"question": "..."}`) |
| `GET` | `/status` | Check loaded documents |
| `DELETE` | `/reset` | Clear all documents |

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | Your OpenAI API key (required) |
