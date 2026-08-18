Cerebro Logging Guide (plain text log)
======================================

Overview
--------
Cerebro writes a single plain-text log file by default (cerebro.log in the project root) containing human-readable entries. Each entry is one line, with an ISO UTC timestamp, a level, a component identifier, a concise message, and a JSON metadata blob.

Format
------
[timestamp] [LEVEL] component: message | {json metadata}

Example:
2026-08-11T10:05:43.224Z [INFO] llm_service: Sending request to LLM | {"model":"gpt-4","prompt_preview":"Generate a concise support..."}

Why plain text?
---------------
- Easy to inspect with tail, grep, less
- Simple to ship with incident reports
- Append-only for auditability

What is logged
--------------
1. Incoming and processed events
   - component: api.events / context_engine
   - message: Incoming event POST / Event processed
   - metadata: event_type, source, case_id, recommendations

2. Database activity related to key entities
   - component: context_engine / rag_service / api.cases
   - message: Stored event, Stored document metadata, Stored transcription
   - metadata: table name, record id, query previews

3. Audio and screenshot lifecycle
   - component: desktop.audio_recorder / screenpipe_client / api.audio
   - message: Recording started/stopped, Screenshot requested/received
   - metadata: file paths, screenshot ids, durations

4. LLM requests and responses
   - component: llm_service
   - message: Sending request to LLM / Received response from LLM / LLM request failed
   - metadata: model, prompt_preview (first 400 chars), duration_s, tokens (when available), error

5. Qdrant (RAG) index/search
   - component: rag_service
   - message: Indexing document / Qdrant search completed / Search failed
   - metadata: title, source, vector_id, num_results, error

6. Errors and warnings
   - component: any
   - message: error details
   - metadata: stack/exception message

7. Operational notes
   - component: api.audio, screenpipe_client
   - message: Listing or retrieving records
   - metadata: counts, limits

Error handling and root causes
------------------------------
Log messages include errors and minimal troubleshooting hints. Common failures and log signatures:

1. Database connection / migration errors
   - "[ERROR] context_engine: Failed to store event" + DB error
   - Likely cause: DATABASE_URL misconfigured, DB not running, permissions
   - Remedy: check .env, run `psql` or `sqlite3` to verify connection, run migrations

2. LLM failures (network or auth)
   - "[ERROR] llm_service: LLM request failed" + error
   - Likely cause: OPENAI_API_KEY missing/invalid, network or rate-limit
   - Remedy: verify key, test curl to provider, check provider dashboard for rate limits

3. Qdrant connectivity
   - "[ERROR] rag_service: Failed to index document" or "Search failed"
   - Likely cause: Qdrant not reachable, collection config mismatch
   - Remedy: ensure QDRANT_URL reachable, check collection exists

4. Audio recording issues
   - "[WARN] desktop.audio_recorder: VAD sampling error" or "Error: sounddevice not installed"
   - Likely cause: missing OS audio driver or missing Python packages
   - Remedy: install sounddevice/soundfile, grant microphone permission

5. Screenpipe / OCR failures
   - "[ERROR] screenpipe_client: Error fetching OCR"
   - Likely cause: Screenpipe not running, OCR service down
   - Remedy: start Screenpipe, ensure endpoint correct

Installing and enabling detailed logs
-------------------------------------
1. Configure log path in backend/.env (optional):
   CEREBRO_LOG_PATH=C:\path\to\cerebro.log
2. By default logs go to `cerebro.log` in the working directory.
3. Ensure the API process user has write permission to the log file/directory.

Sample log walkthrough (end-to-end)
-----------------------------------
1. Browser extension opens Salesforce case -> API receives event:
   - [INFO] api.events: Incoming event POST | {"event_type":"CRM_CASE_OPENED","case_id":"500"}
2. Context engine persists event:
   - [INFO] context_engine: Received event | {"type":"CRM_CASE_OPENED","case_id":"500"}
   - [INFO] context_engine: Stored event id | {"id":42}
3. Screenpipe detects new screenshot and OCRs it:
   - [INFO] screenpipe_client: Requesting screenshots | {"limit":1}
   - [INFO] screenpipe_client: Received screenshots response | {"count":1}
   - [INFO] screenpipe_client: Requesting OCR for screenshot | {"id":"s123"}
   - [INFO] screenpipe_client: Received OCR | {"len_chars":120}
4. Call starts; audio recorder records and posts transcript:
   - [INFO] desktop.audio_recorder: Started recording | {"file":"C:\...wav"}
   - [INFO] desktop.audio_recorder: Stopped recording | {"duration": 174.4}
   - [INFO] desktop.audio_recorder: Transcribed with faster-whisper | {"provider":"faster-whisper","segments":12}
   - [INFO] api.events: Incoming event POST | {"event_type":"TRANSCRIPT"}
5. ContextEngine handles TRANSCRIPT, LLM called:
   - [INFO] llm_service: Sending request to LLM | {"model":"gpt-4","prompt_preview":"..."}
   - [INFO] llm_service: Received response from LLM | {"duration_s":1.2}
   - [INFO] context_engine: Updated Case.ai_summary | {"case_id":"500"}
6. RAG search for relevant docs (if triggered):
   - [INFO] rag_service: Search requested | {"query_preview":"Outlook error 0x80040115"}
   - [INFO] rag_service: Qdrant search completed | {"num_results":3}

Log retention & rotation
------------------------
- For long-running deployments, configure logrotate on Linux or Windows scheduled tasks to rotate cerebro.log daily and keep N copies.
- The logger is a simple append-only writer; for production use, replace with a structured logging system (e.g. ELK, Splunk, or a cloud logging service).

Privacy note
------------
- Logs contain transcript previews and metadata. Configure retention and encryption accordingly.
- Consider redacting sensitive PII before writing full transcripts to logs.

Where to find logs
------------------
- Default: `cerebro.log` in the working directory of the backend process (see CEREBRO_LOG_PATH to override)

EOF
