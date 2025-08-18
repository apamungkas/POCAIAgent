import streamlit as st
from dotenv import load_dotenv
import os
import json
import base64
import requests
import re  # for extracting JSON blocks

# Load environment variables
load_dotenv()

# Import our modules
from auth.msal_auth import auth
from auth.rbac import rbac, UserRole
# from ai_agent.foundry_client import ai_agent


def peek_jwt(token: str):
    """Decode JWT payload for debugging."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {"_error": "Not a JWT"}
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        return payload
    except Exception as e:
        return {"_error": str(e)}


# Helpers to detect & prepare payloads --------------------------------

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*({.*?})\s*```", re.DOTALL | re.IGNORECASE)

def _coerce_list_str_emails(v):
    """Accept list[str] or comma/semicolon-separated string → list[str]."""
    if v is None:
        return []
    if isinstance(v, list):
        return [e.strip() for e in v if isinstance(e, str) and e.strip()]
    if isinstance(v, str):
        parts = re.split(r"[;,]", v)
        return [p.strip() for p in parts if p.strip()]
    return []

def try_extract_email_payload(text: str):
    """
    Look for a JSON object in the assistant output that has subject & bodyHtml.
    Returns dict or None.
    """
    if not text or not isinstance(text, str):
        return None

    candidates = []
    for m in JSON_FENCE_RE.finditer(text):
        candidates.append(m.group(1))

    if not candidates:
        m = re.search(r"(\{[^{}]*?(\"subject\"|\'subject\')[^{}]*?(\"bodyHtml\"|\'bodyHtml\')[\s\S]*?\})", text)
        if m:
            candidates.append(m.group(1))

    for raw in candidates:
        try:
            obj = json.loads(raw)
            subj = obj.get("subject")
            body = obj.get("bodyHtml")
            recp = obj.get("recipients")
            if isinstance(subj, str) and isinstance(body, str):
                return {
                    "subject": subj.strip(),
                    "bodyHtml": body,
                    "recipients": _coerce_list_str_emails(recp)
                }
        except Exception:
            continue

    return None

def try_extract_meeting_payload(text: str):
    """
    Look for a JSON object in assistant output for scheduling:
    Accepts both:
      - requiredAttendees[] or recipients[]  → requiredAttendees[]
      - body or bodyHtml                     → body
    Expected keys: subject, timeZone, start, end (timeZone defaults if missing).
    Returns dict or None.
    """
    if not text or not isinstance(text, str):
        return None

    keys_required = {"subject", "timeZone", "start", "end"}
    candidates = []
    for m in JSON_FENCE_RE.finditer(text):
        candidates.append(m.group(1))
    if not candidates:
        # Heuristic: must contain subject + start + end
        m = re.search(r"(\{[^{}]*?(\"subject\"|\'subject\')[\s\S]*?(\"start\"|\'start\')[\s\S]*?(\"end\"|\'end\')[\s\S]*?\})", text)
        if m:
            candidates.append(m.group(1))

    for raw in candidates:
        try:
            obj = json.loads(raw)

            # Allow timeZone to be absent in source and default later
            has_min_required = {"subject", "start", "end"}.issubset(set(obj.keys()))
            if not (has_min_required or keys_required.issubset(set(obj.keys()))):
                continue

            # --- alias normalization (minimal change) ---
            # body/bodyHtml → body
            body_html = obj.get("bodyHtml")
            body_text = obj.get("body")
            body_value = body_text if isinstance(body_text, str) else (body_html if isinstance(body_html, str) else "")

            # recipients/requiredAttendees → requiredAttendees
            req = obj.get("requiredAttendees")
            rec = obj.get("recipients")
            required_attendees = _coerce_list_str_emails(req if req is not None else rec)

            optional_attendees = _coerce_list_str_emails(obj.get("optionalAttendees"))

            payload = {
                "subject": (obj.get("subject") or "").strip(),
                "body": body_value,
                "timeZone": obj.get("timeZone", "SE Asia Standard Time"),
                "start": obj.get("start"),
                "end": obj.get("end"),
                "calendarId": obj.get("calendarId", "Calendar"),
                "requiredAttendees": required_attendees,
                "optionalAttendees": optional_attendees,
                "location": obj.get("location", "Microsoft Teams")
            }
            # basic sanity
            if payload["subject"] and payload["start"] and payload["end"]:
                return payload
        except Exception:
            continue

    return None


# --- NEW: meeting intent detector (minimal) ---
MEETING_INTENT_RE = re.compile(
    r'\b(schedule|book|set\s*up|setup|arrange|create)\b.*\b(meeting|call|teams)\b',
    re.IGNORECASE
)
def is_meeting_intent(text: str | None) -> bool:
    return bool(text and MEETING_INTENT_RE.search(text))


# -------------------- NEW: secured-search helpers (minimal) --------------------

POPULARITY_RE = re.compile(r"(most\s+popular|top[-\s]?selling|best\s+seller|highest\s+units|popular\s+product|top\s+product)", re.I)
REVENUE_Q_RE   = re.compile(r"\b(total\s+revenue|revenue|sales\s+revenue)\b", re.I)
REVENUE_OF_FOR_RE = re.compile(r"\b(?:total\s+revenue|revenue|sales\s+revenue)\s+(?:of|for)\s+\"?([A-Za-z0-9\-\s\+\./%]+?)\"?\s*(\?|$)", re.I)
QUOTED_RE         = re.compile(r"\"([^\"]+)\"")
_REGION_RE        = re.compile(r"\bregion\s*([0-9]+)\b|\b(region[0-9]+)\b", re.I)

def is_popularity_intent(text: str | None) -> bool:
    return bool(text and POPULARITY_RE.search(text or ""))

def is_revenue_intent(text: str | None) -> bool:
    return bool(text and REVENUE_Q_RE.search(text or ""))

def extract_requested_region(text: str | None) -> str | None:
    if not text:
        return None
    m = _REGION_RE.search(text)
    if not m:
        return None
    if m.group(1):
        return f"region{m.group(1)}".lower().strip()
    if m.group(2):
        return m.group(2).lower().strip()
    return None

def extract_product_from_revenue_q(text: str | None) -> str | None:
    if not text:
        return None
    m = REVENUE_OF_FOR_RE.search(text)
    if m:
        return m.group(1).strip()
    m2 = QUOTED_RE.search(text)
    if m2:
        return m2.group(1).strip()
    return None

def post_secured_search(access_token: str, payload: dict) -> tuple[int, dict | str]:
    """
    Call the backend /secured-search (APIM will enforce region/revenue).
    Uses API_BASE only.
    """
    base = (os.getenv("API_BASE", "") or "").rstrip("/")
    if not base:
        return 500, "API_BASE env var is not set"
    endpoint = f"{base}/secured-search"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=60)
        try:
            data = resp.json()
        except Exception:
            data = resp.text
        return resp.status_code, data
    except Exception as e:
        return 500, f"Request error: {e}"


def post_send_as_user(access_token: str, payload: dict) -> tuple[int, dict | str]:
    """
    Call the backend /send-as-user (OBO to Graph).
    Uses API_BASE only.
    """
    base = (os.getenv("API_BASE", "") or "").rstrip("/")
    if not base:
        return 500, "API_BASE env var is not set"
    endpoint = f"{base}/send-as-user"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=60)
        try:
            data = resp.json()
        except Exception:
            data = resp.text
        return resp.status_code, data
    except Exception as e:
        return 500, f"Request error: {e}"

def post_schedule_as_user(access_token: str, payload: dict) -> tuple[int, dict | str]:
    """
    Call the backend /schedule-as-user (OBO to Graph for /me/events).
    Uses API_BASE only.
    """
    base = (os.getenv("API_BASE", "") or "").rstrip("/")
    if not base:
        return 500, "API_BASE env var is not set"
    endpoint = f"{base}/schedule-as-user"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=60)
        try:
            data = resp.json()
        except Exception:
            data = resp.text
        return resp.status_code, data
    except Exception as e:
        return 500, f"Request error: {e}"


class AzureAIFoundryApp:
    """Main Streamlit application for Azure AI Foundry with Entra ID authentication."""
    
    def __init__(self):
        self.setup_page_config()
        self.load_custom_css()
        
    def setup_page_config(self):
        st.set_page_config(
            page_title="AI Chatbot Demo",
            page_icon="💬",
            layout="wide",
            initial_sidebar_state="expanded"
        )
    
    def load_custom_css(self):
        try:
            with open("static/style.css") as f:
                st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
        except FileNotFoundError:
            pass  
    
    def display_header(self):
        st.markdown("""
        <div class="main-header">
            <h1>Telkom AI Agent</h1>
            <p>Secure AI Assistant with Microsoft Entra ID Authentication</p>
        </div>
        """, unsafe_allow_html=True)
    
    def handle_authentication_flow(self):
        query_params = st.query_params
        
        if "code" in query_params:
            auth_code = query_params["code"]
            
            with st.spinner("Authenticating..."):
                token_result = auth.acquire_token_by_auth_code(auth_code)
                
                if token_result and "access_token" in token_result:
                    # Save tokens into session
                    st.session_state["access_token"] = token_result["access_token"]
                    st.session_state["token_info"] = token_result
                    st.session_state["id_token_claims"] = token_result.get("id_token_claims", {})

                    # Get user info (Graph if available; otherwise from claims)
                    user_info = auth.get_user_info(token_result["access_token"])
                    if not user_info:
                        claims = token_result.get("id_token_claims", {})
                        user_info = {
                            "displayName": claims.get("name", "Unknown"),
                            "userPrincipalName": claims.get("preferred_username", ""),
                            "groups": claims.get("groups", [])
                        }

                    if user_info:
                        st.session_state["authenticated"] = True
                        st.session_state["user_info"] = user_info
                        st.session_state["user_role"] = rbac.determine_user_role(user_info.get("groups", []))
                        
                        st.query_params.clear()
                        st.rerun()
                    else:
                        st.error("Failed to retrieve user information.")
                        return False
                else:
                    st.error("Authentication failed. Please try again.")
                    return False
        return True

    # --- make sure access_token exists whenever token_info is valid ---
    def ensure_access_token(self):
        tok = st.session_state.get("access_token")
        if tok:
            return
        token_info = st.session_state.get("token_info", {})
        if token_info and "access_token" in token_info:
            st.session_state["access_token"] = token_info["access_token"]

    def display_login_page(self):
        col1, col2, col3 = st.columns([1, 2, 1])
        
        with col2:
            st.markdown("""
            <div class="auth-container">
                <h2>Authentication Required</h2>
                <p>Please sign in with your Microsoft account to access the AI Agent.</p>
            </div>
            """, unsafe_allow_html=True)
            
            if st.button("Sign in with Microsoft", use_container_width=True):
                auth_url = auth.get_auth_url()
                st.markdown(f'<meta http-equiv="refresh" content="0;url={auth_url}">', unsafe_allow_html=True)
    
    def display_user_info_sidebar(self):
        user_info = st.session_state.get("user_info", {})
        user_role = st.session_state.get("user_role", UserRole.UNAUTHORIZED)
        
        with st.sidebar:
            st.markdown("### User Information")
            st.markdown(f"""
            <div class="sidebar-info">
                <p><strong>Name:</strong> {user_info.get('displayName', 'Unknown')}</p>
                <p><strong>Role:</strong> {rbac.get_role_display_name(user_role)}</p>
            </div>
            """, unsafe_allow_html=True)
            
            # Debug: show JWT claims + raw token (copyable)
            access_token = st.session_state.get("access_token")
            with st.expander("Access Token (debug)"):
                if access_token:
                    claims = peek_jwt(access_token)
                    st.markdown("**Claims:**")
                    st.json({k: claims.get(k) for k in ["aud", "scp", "roles", "tid", "iss", "groups"]})
                    st.markdown("**Raw token:**")
                    st.text_area("access_token", value=access_token, height=120)
                else:
                    st.info("No access token in session yet.")
                    if st.button("Load token from session"):
                        self.ensure_access_token()
                        st.rerun()

            # Persistent API request/response debug
            with st.expander("Last API request/response"):
                dbg = st.session_state.get("last_api_debug")
                if dbg:
                    st.write("Status code:", dbg.get("status_code"))
                    st.write("Endpoint:", dbg.get("endpoint"))
                    st.markdown("**Request payload:**")
                    st.json(dbg.get("request"))
                    st.markdown("**Response JSON / Text:**")
                    if isinstance(dbg.get("response"), dict):
                        st.json(dbg.get("response"))
                    else:
                        st.code(dbg.get("response"))
                else:
                    st.info("No API debug info yet. Ask a question to populate this.")

            # Mail debug block
            with st.expander("Last Mail send (OBO)"):
                mdbg = st.session_state.get("last_mail_debug")
                if mdbg:
                    st.write("Status code:", mdbg.get("status_code"))
                    st.write("Endpoint:", mdbg.get("endpoint"))
                    st.markdown("**Request payload:**")
                    st.json(mdbg.get("request"))
                    st.markdown("**Response JSON / Text:**")
                    if isinstance(mdbg.get("response"), dict):
                        st.json(mdbg.get("response"))
                    else:
                        st.code(mdbg.get("response"))
                else:
                    st.info("No mail calls yet.")

            # Meeting debug block
            with st.expander("Last Meeting schedule (OBO)"):
                sdbg = st.session_state.get("last_meeting_debug")
                if sdbg:
                    st.write("Status code:", sdbg.get("status_code"))
                    st.write("Endpoint:", sdbg.get("endpoint"))
                    st.markdown("**Request payload:**")
                    st.json(sdbg.get("request"))
                    st.markdown("**Response JSON / Text:**")
                    if isinstance(sdbg.get("response"), dict):
                        st.json(sdbg.get("response"))
                    else:
                        st.code(sdbg.get("response"))
                else:
                    st.info("No meeting calls yet.")

            st.markdown("---")
            st.markdown("### Chat Controls")
            
            if st.button("Clear Chat", use_container_width=True):
                if "chat_history" in st.session_state:
                    st.session_state["chat_history"] = []
                st.session_state.pop("pending_email", None)
                st.session_state.pop("pending_meeting", None)
                st.rerun()
            
            if st.button("Logout", use_container_width=True):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                logout_url = auth.logout()
                st.markdown(f'<meta http-equiv="refresh" content="0;url={logout_url}">', unsafe_allow_html=True)

    # Render a simple send-as-user panel if a payload exists
    def display_email_send_panel(self):
        # NEW: suppress email panel when a meeting draft exists
        if st.session_state.get("pending_meeting"):
            return

        pending = st.session_state.get("pending_email")
        if not pending:
            return

        st.markdown("### Send email as you (Microsoft Graph OBO)")
        with st.form("send_email_form", clear_on_submit=False):
            recipients_str = st.text_input(
                "Recipients (comma or semicolon separated)",
                value="; ".join(pending.get("recipients", []))
            )
            subject = st.text_input("Subject", value=pending.get("subject", ""))
            body_html = st.text_area("Body (HTML)", value=pending.get("bodyHtml", ""), height=240)
            submitted = st.form_submit_button("Send email as me")

        if submitted:
            recipients = _coerce_list_str_emails(recipients_str)
            if not recipients:
                st.error("Please provide at least one recipient email.")
                return
            tok = st.session_state.get("access_token")
            if not tok:
                st.error("Missing access token. Please log in again.")
                return

            mail_payload = {"recipients": recipients, "subject": subject, "bodyHtml": body_html}
            status, data = post_send_as_user(tok, mail_payload)

            endpoint = f"{(os.getenv('API_BASE','').rstrip('/'))}/send-as-user"
            st.session_state["last_mail_debug"] = {
                "endpoint": endpoint,
                "request": mail_payload,
                "status_code": status,
                "response": data
            }

            if status == 200:
                st.success("Email sent successfully as your account.")
                st.session_state.pop("pending_email", None)
            else:
                st.error(f"Failed to send email (status {status}). See debug in sidebar.")

    # Render a simple schedule-as-user panel if a payload exists
    def display_meeting_schedule_panel(self):
        pending = st.session_state.get("pending_meeting")
        if not pending:
            return

        st.markdown("### Schedule meeting as you (Microsoft Graph OBO)")
        with st.form("schedule_meeting_form", clear_on_submit=False):
            subject = st.text_input("Subject", value=pending.get("subject", ""))
            body_html = st.text_area("Body (HTML)", value=pending.get("body", ""), height=200)
            time_zone = st.text_input("Time Zone", value=pending.get("timeZone", "SE Asia Standard Time"))
            start = st.text_input("Start (YYYY-MM-DDTHH:mm:ss)", value=pending.get("start", ""))
            end = st.text_input("End (YYYY-MM-DDTHH:mm:ss)", value=pending.get("end", ""))
            calendar_id = st.text_input("Calendar Id", value=pending.get("calendarId", "Calendar"))
            required_str = st.text_input("Required attendees (comma/semicolon)", value="; ".join(pending.get("requiredAttendees", [])))
            optional_str = st.text_input("Optional attendees (comma/semicolon)", value="; ".join(pending.get("optionalAttendees", [])))
            location = st.text_input("Location", value=pending.get("location", "Microsoft Teams"))

            submitted = st.form_submit_button("Schedule as me")

        if submitted:
            req = _coerce_list_str_emails(required_str)
            if not req:
                st.error("Please provide at least one required attendee email.")
                return
            tok = st.session_state.get("access_token")
            if not tok:
                st.error("Missing access token. Please log in again.")
                return

            meeting_payload = {
                "subject": subject,
                "body": body_html,
                "timeZone": time_zone,
                "start": start,
                "end": end,
                "calendarId": calendar_id,
                "requiredAttendees": req,
                "optionalAttendees": _coerce_list_str_emails(optional_str),
                "location": location
            }

            status, data = post_schedule_as_user(tok, meeting_payload)

            endpoint = f"{(os.getenv('API_BASE','').rstrip('/'))}/schedule-as-user"
            st.session_state["last_meeting_debug"] = {
                "endpoint": endpoint,
                "request": meeting_payload,
                "status_code": status,
                "response": data
            }

            if status == 200:
                link = ""
                if isinstance(data, dict):
                    jl = data.get("joinUrl")
                    wl = data.get("webLink")
                    if jl:
                        link = f" Join link: {jl}"
                    elif wl:
                        link = f" Web link: {wl}"
                st.success(f"Meeting scheduled successfully as your account.{link}")
                st.session_state.pop("pending_meeting", None)
            else:
                st.error(f"Failed to schedule meeting (status {status}). See debug in sidebar.")

    def display_main_chat_interface(self):
        user_role = st.session_state.get("user_role", UserRole.UNAUTHORIZED)
        
        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []
        
        st.markdown("### AI Agent")
        st.info(f"**Access Level:** {rbac.get_role_display_name(user_role)}")
        
        chat_container = st.container()
        with chat_container:
            for message in st.session_state["chat_history"]:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

        user_message = st.chat_input("Ask me anything...", key="chat_input")
        
        if user_message:
            st.session_state["chat_history"].append({"role": "user", "content": user_message})
            with st.chat_message("user"):
                st.write(user_message)
            
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        apim_base = (os.getenv("API_BASE", "") or "").strip()
                        tok = st.session_state.get("access_token")

                        # ---------------- NEW: secured-search routing ----------------
                        call_secured = False
                        sec_payload = None

                        if is_popularity_intent(user_message):
                            call_secured = True
                            sec_payload = {"operation": "popular_product"}
                            req_reg = extract_requested_region(user_message)
                            if req_reg:
                                sec_payload["requested_region"] = req_reg

                        elif is_revenue_intent(user_message):
                            call_secured = True
                            prod = extract_product_from_revenue_q(user_message)
                            req_reg = extract_requested_region(user_message)

                            # If user asks "revenue of most popular product"
                            if (not prod) and POPULARITY_RE.search(user_message or ""):
                                # step 1: ask secured-search for top product (respect region ask)
                                step1_payload = {"operation": "popular_product"}
                                if req_reg:
                                    step1_payload["requested_region"] = req_reg
                                s1, d1 = post_secured_search(tok, step1_payload)
                                st.session_state["last_api_debug"] = {
                                    "endpoint": f"{apim_base.rstrip('/')}/secured-search",
                                    "request": step1_payload,
                                    "status_code": s1,
                                    "response": d1
                                }
                                if s1 == 200 and isinstance(d1, dict) and d1.get("data") and d1["data"].get("Product"):
                                    prod = d1["data"]["Product"]
                                else:
                                    # likely denial or no data → show and stop
                                    step1_msg = (d1.get("answer_md") or d1.get("answer")) if isinstance(d1, dict) else str(d1)
                                    if step1_msg:
                                        st.markdown(step1_msg)
                                        st.session_state["chat_history"].append({"role": "assistant", "content": step1_msg})
                                        st.rerun()

                            sec_payload = {"operation": "product_revenue"}
                            if prod:
                                sec_payload["product"] = prod
                            if req_reg:
                                sec_payload["requested_region"] = req_reg

                        if call_secured:
                            s, d = post_secured_search(tok, sec_payload)
                            st.session_state["last_api_debug"] = {
                                "endpoint": f"{apim_base.rstrip('/')}/secured-search",
                                "request": sec_payload,
                                "status_code": s,
                                "response": d
                            }
                            if s == 200 and isinstance(d, dict) and (d.get("answer_md") or d.get("answer")):
                                ai_md = d.get("answer_md") or d.get("answer")
                                st.markdown(ai_md)
                                st.session_state["chat_history"].append({"role": "assistant", "content": ai_md})
                                st.rerun()
                            else:
                                ai_response = f"Error {s}: {d}"
                                st.write(ai_response)
                                st.session_state["chat_history"].append({"role": "assistant", "content": ai_response})
                                st.rerun()
                        # ---------------- END secured-search routing ----------------

                        # Default: /chat
                        apim_endpoint = f"{apim_base}/chat"
                        st.caption(f"Calling: {apim_endpoint}")  # small hint for debugging

                        role_header = "admin" if user_role == UserRole.ADMIN else "user"
                        headers = {
                            "Authorization": f"Bearer {tok}",
                            "Content-Type": "application/json",
                            "x-user-role": role_header,
                        }
                        payload = {"input": user_message}
                        if st.session_state.get("thread_id"):
                            payload["thread_id"] = st.session_state["thread_id"]

                        st.session_state["last_api_debug"] = {
                            "endpoint": apim_endpoint,
                            "request": payload
                        }

                        resp = requests.post(apim_endpoint, json=payload, headers=headers, timeout=60)

                        try:
                            resp_json = resp.json()
                        except Exception:
                            resp_json = resp.text
                        st.session_state["last_api_debug"].update({
                            "status_code": resp.status_code,
                            "response": resp_json
                        })

                        if resp.status_code == 200:
                            data = resp_json if isinstance(resp_json, dict) else {}
                            ai_md = data.get("answer_md")
                            ai_text = data.get("answer", "No response from AI")

                            if ai_md:
                                st.markdown(ai_md)
                                rendered = ai_md
                            else:
                                st.write(ai_text)
                                rendered = ai_text

                            sources = data.get("sources", [])
                            urls_only = [s.get("url", "") for s in sources if isinstance(s, dict) and s.get("url")]
                            if urls_only:
                                lines = ["**Sources:**"]
                                for u in urls_only:
                                    lines.append(f"- <{u}>")
                                sources_md = "\n".join(lines)
                                st.markdown(sources_md)
                                rendered = f"{rendered}\n\n{sources_md}"

                            if data.get("thread_id"):
                                st.session_state["thread_id"] = data["thread_id"]

                            st.session_state["chat_history"].append({"role": "assistant", "content": rendered})

                            # Intent-aware routing: prefer meeting when requested
                            full_text = (ai_md or ai_text) or ""
                            meeting_requested = is_meeting_intent(user_message) or is_meeting_intent(full_text)

                            draft_meeting = try_extract_meeting_payload(full_text)
                            draft_email   = try_extract_email_payload(full_text)

                            if meeting_requested:
                                if draft_meeting:
                                    st.session_state["pending_meeting"] = draft_meeting
                                    st.session_state.pop("pending_email", None)
                                else:
                                    prefill = {
                                        "subject": (draft_email or {}).get("subject", ""),
                                        "body": "",
                                        "timeZone": "SE Asia Standard Time",
                                        "start": "",
                                        "end": "",
                                        "calendarId": "Calendar",
                                        "requiredAttendees": (draft_email or {}).get("recipients", []),
                                        "optionalAttendees": [],
                                        "location": "Microsoft Teams"
                                    }
                                    st.session_state["pending_meeting"] = prefill
                                    st.session_state.pop("pending_email", None)
                            else:
                                if draft_meeting:
                                    st.session_state["pending_meeting"] = draft_meeting
                                    st.session_state.pop("pending_email", None)
                                elif draft_email:
                                    st.session_state["pending_email"] = draft_email

                        else:
                            ai_response = f"Error {resp.status_code}: {resp.text}"
                            st.write(ai_response)
                            st.session_state["chat_history"].append({"role": "assistant", "content": ai_response})
                        
                    except Exception as e:
                        error_msg = f"Error: {str(e)}"
                        st.error(error_msg)
                        st.session_state["chat_history"].append({"role": "assistant", "content": error_msg})
            st.rerun()

        # Show action panels if we have drafts ready (meeting first)
        self.display_meeting_schedule_panel()
        self.display_email_send_panel()
    
    def check_authentication_status(self):
        if "authenticated" not in st.session_state:
            return False
        
        token_info = st.session_state.get("token_info", {})
        if not auth.is_token_valid(token_info):
            user_info = st.session_state.get("user_info", {})
            if user_info:
                account = {
                    "username": user_info.get("userPrincipalName", ""),
                    "home_account_id": user_info.get("id", "")
                }
                refreshed_token = auth.acquire_token_silent(account)
                if refreshed_token:
                    st.session_state["token_info"] = refreshed_token
                    st.session_state["access_token"] = refreshed_token["access_token"]
                    return True
                else:
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    return False
        else:
            # ensure access_token present if token is valid
            self.ensure_access_token()
        return True
    
    def run(self):
        self.display_header()
        if not self.handle_authentication_flow():
            return
        if not self.check_authentication_status():
            self.display_login_page()
            return
        if st.session_state.get("user_role", UserRole.UNAUTHORIZED) == UserRole.UNAUTHORIZED:
            st.error("Access Denied: You don't have the required permissions to use this application.")
            if st.button("Logout"):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()
            return
        self.display_user_info_sidebar()
        self.display_main_chat_interface()


def main():
    try:
        app = AzureAIFoundryApp()
        app.run()
    except Exception as e:
        st.error(f"Application error: {str(e)}")


if __name__ == "__main__":
    main()
