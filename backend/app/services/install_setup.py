"""
Setup chosen in the installer, applied and checked without a window.

The installer's own pages ask for the AI provider, the AWS sign-in, the voice
and the default agent's look, and save them as JSON. After copying the files it
runs ``CerebroSetupWizard.exe --apply <choices.json> --result <result.json>``,
which calls :func:`run`: the settings are saved through the normal settings
store (so they're validated exactly as in Settings), the database is opened,
the AI provider is asked a test question, and every step's outcome is written
to the result file, which the installer's last page shows. A step that fails is
reported, never hidden; the person can fix it later in Settings.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

#: Settings the installer may set (anything else in the file is ignored).
ALLOWED = {
    "LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "OLLAMA_URL", "OLLAMA_MODEL",
    "BEDROCK_REGION", "BEDROCK_MODEL_ID", "BEDROCK_AUTH_MODE", "BEDROCK_API_KEY", "BEDROCK_AWS_PROFILE",
    "BEDROCK_AWS_ACCESS_KEY_ID", "BEDROCK_AWS_SECRET_ACCESS_KEY", "BEDROCK_AWS_SESSION_TOKEN",
    "VOICE_ENABLED", "VOICE_SPEAK_REPLIES", "VOICE_PROVIDER", "VOICE_NAME", "VOICE_PERSONALITY",
}


def _step(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"step": name, "ok": bool(ok), "detail": str(detail)[:300]}


def run(choices: Dict[str, Any], verify: bool = True) -> Dict[str, Any]:
    """Apply the installer's choices and check them. Returns {"ok", "steps"}."""
    from app.core import settings_store

    steps: List[Dict[str, Any]] = []
    changes = {k: v for k, v in (choices.get("settings") or {}).items()
               if k in ALLOWED and v not in (None, "")}
    if changes:
        try:
            result = settings_store.update(changes)
            if result.get("ok"):
                steps.append(_step("settings", True, f"Saved {len(result.get('applied') or changes)} settings."))
            else:
                errors = result.get("errors") or {}
                steps.append(_step("settings", False, "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])
                                   or "The settings were rejected."))
        except Exception as exc:  # noqa: BLE001 - reported
            steps.append(_step("settings", False, f"Couldn't save the settings: {exc}"))
    if not verify:
        return {"ok": all(s["ok"] for s in steps), "steps": steps}

    # The database: created or upgraded now, so the first start is quick.
    try:
        from app.core.config import settings
        from app.core.database import init_db, probe_database

        probe = probe_database(settings.DATABASE_URL, initialize=True)
        if probe.get("ok"):
            init_db()
        steps.append(_step("database", probe.get("ok"), probe.get("detail") or "Database ready."))
    except Exception as exc:  # noqa: BLE001
        steps.append(_step("database", False, f"Couldn't open the database: {exc}"))

    # The default agent's look and name.
    look = choices.get("agent") or {}
    if look:
        try:
            from app.core.database import SessionLocal
            from app.services import agents

            with SessionLocal() as db:
                agent = agents.default_agent(db)
                fields: Dict[str, Any] = {}
                if look.get("name"):
                    fields["name"] = str(look["name"])[:60]
                face = look.get("face")
                if face and str(face).startswith("builtin"):
                    from app.api.faces import builtin_faces

                    models = builtin_faces()
                    # "builtin" alone: the first built-in model.
                    meta = next((m for m in models if m["id"] == face), models[0] if models else None)
                    if meta:
                        fields["face"] = "vrm"
                        fields["face_style"] = {"model": meta["id"], "entry": meta["entry"],
                                                **({"framing": meta["framing"]} if meta.get("framing") else {}),
                                                **({"expressions": meta["expressions"]} if meta.get("expressions") else {})}
                elif face in ("anime", "brain"):
                    fields["face"] = face
                if look.get("personality"):
                    fields["personality"] = {"preset": look["personality"]}
                if fields:
                    agents.update(db, agent, **fields)
                steps.append(_step("agent", True, f"{agent.name} is ready."))
        except Exception as exc:  # noqa: BLE001
            steps.append(_step("agent", False, f"Couldn't set up the agent: {exc}"))

    # The AI provider: one real test question.
    try:
        from app.core.config import settings

        if (settings.LLM_PROVIDER or "").strip():
            from app.services.llm_service import LLMService

            test = LLMService().test_connection()
            steps.append(_step("ai", test.get("ok"), test.get("detail") or ("The model answered." if test.get("ok")
                                                                              else "The model didn't answer.")))
        else:
            steps.append(_step("ai", False, "No AI provider chosen yet — pick one in Settings → AI Provider."))
    except Exception as exc:  # noqa: BLE001
        steps.append(_step("ai", False, f"Couldn't reach the AI provider: {exc}"))

    # Offline speech and voice, which ship inside the installer.
    try:
        from app.services import kokoro_tts, speech

        stt = speech.model_folder("base.en") is not None
        steps.append(_step("speech", stt, "Speech recognition is built in and works offline." if stt
                           else "The speech model is missing from this install."))
        ok, why = kokoro_tts.available()
        steps.append(_step("voice", ok, "The offline voice is built in." if ok else why))
    except Exception as exc:  # noqa: BLE001
        steps.append(_step("voice", False, str(exc)))

    if all(s["ok"] for s in steps):
        try:
            from app.core import settings_store

            settings_store.mark_setup_complete()
        except Exception:  # noqa: BLE001 - the first start will offer setup again
            pass
    return {"ok": all(s["ok"] for s in steps), "steps": steps}


def run_files(choices_path: str, result_path: str, verify: bool = True) -> int:
    """Read the installer's choices, apply them, write the result. Never raises."""
    try:
        choices = json.loads(Path(choices_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        result = {"ok": False, "steps": [_step("read", False, f"Couldn't read the installer's choices: {exc}")]}
    else:
        try:
            result = run(choices, verify=verify)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "steps": [_step("setup", False, str(exc))]}
        finally:
            # The file can hold keys: it isn't kept.
            try:
                Path(choices_path).unlink()
            except OSError:
                pass
    Path(result_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = [f"{'OK  ' if s['ok'] else 'FIX '} {s['step']}: {s['detail']}" for s in result["steps"]]
    Path(result_path).with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if result["ok"] else 2
