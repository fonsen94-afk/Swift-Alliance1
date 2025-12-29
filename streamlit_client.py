"""
Streamlit client for SWIFT-Alliance-like microservices.

Usage:
 - Put API endpoints and credentials into Streamlit Secrets:
   [API]
   base_url = "https://api.example.com"         # gateway for Message/Validation/Transmit
   auth_url = "https://auth.example.com"        # auth service base
   client_id = "<client id>"
   client_secret = "<client secret>"
   api_key = "<optional api key>"

 - Requirements (add to requirements.txt):
   streamlit
   requests
   reportlab
   pillow
   cairosvg  # optional for SVG->PNG conversion if using SVG logos
   xmlschema  # optional if you do validation client-side

 - Run locally:
   streamlit run streamlit_client.py
"""
import os
import time
import io
import json
import uuid
import logging
import datetime
from decimal import Decimal
from typing import Optional, Dict, Any

import streamlit as st
import requests

# Optional libs for PDF generation
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    HAS_REPORTLAB = True
except Exception:
    HAS_REPORTLAB = False

# Basic logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("swift_client")

# --- Helpers: config from st.secrets or fallback demo mode -----------------------
API = st.secrets.get("API", {}) if hasattr(st, "secrets") else {}
BASE_URL = API.get("base_url")  # e.g., https://api.yourdomain.com
AUTH_URL = API.get("auth_url")
CLIENT_ID = API.get("client_id")
CLIENT_SECRET = API.get("client_secret")
API_KEY = API.get("api_key")

DEMO_MODE = not bool(BASE_URL and AUTH_URL)

# --- Small UI styling (Oracle-like) --------------------------------------------
_ORACLE_RED = "#D00000"
st.markdown(f"""
<style>
.header-bar {{ background: linear-gradient(90deg, {_ORACLE_RED}, #b00000); color:white; padding:10px; border-radius:6px; }}
.login-logo {{ display:block; margin-left:auto; margin-right:auto; max-width:260px; }}
.dos-box {{ background:#000; color:#00FF70; padding:12px; border-radius:6px; font-family:monospace; white-space:pre-wrap; }}
</style>
""", unsafe_allow_html=True)

# --- Auth helpers ---------------------------------------------------------------
def api_post(path: str, token: Optional[str]=None, data=None, files=None, timeout=30):
    url = BASE_URL.rstrip("/") + "/" + path.lstrip("/")
    headers = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.post(url, headers=headers, json=data, files=files, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

def api_get(path: str, token: Optional[str]=None, params=None, timeout=30):
    url = BASE_URL.rstrip("/") + "/" + path.lstrip("/")
    headers = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    resp.raise_for_status()
    # try JSON, else return raw bytes
    try:
        return resp.json()
    except Exception:
        return resp.content

def auth_login(username: str, password: str) -> Optional[str]:
    """
    Authenticate against Auth Service. Return JWT token or None in demo mode.
    """
    if DEMO_MODE:
        # demo token (not secure) — in real deployment use real Auth endpoint
        return f"demo-token-{username}"
    try:
        url = AUTH_URL.rstrip("/") + "/auth/login"
        payload = {"username": username, "password": password, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("access_token") or data.get("token")
    except Exception as e:
        st.error(f"Auth error: {e}")
        logger.exception("Auth login failed")
        return None

# --- PDF helper (local fallback) ------------------------------------------------
def build_formal_text(message_type: str, message_body: str, sender_info: Dict[str,str], start_ts: str, end_ts: str, account_number: str) -> str:
    parts = [
        "INSTANT TYPE AND TRANSMISSION: INSTANT",
        "",
        "MESSAGE HEADER",
        f"Message Type: {message_type}",
        f"Reference: {datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        f"Sender BIC: {sender_info.get('bic','')}",
        f"Sender Bank: {sender_info.get('bank_name','')}",
        f"Sender Address: {sender_info.get('bank_address','')}",
        f"Account Name: {sender_info.get('account_name','')}",
        f"Account IBAN: {sender_info.get('account_iban','')}",
        f"Selected Account: {account_number}",
        "",
        "MESSAGE TEXT",
        message_body,
        "",
        "MESSAGE HAS BEEN TRANSMITTED SUCCESSFULLY",
        "CONFIRMED & RECEIVED",
        "",
        f"Start Time: {start_ts}",
        f"End Time:   {end_ts}",
    ]
    return "\n".join(parts)

def generate_pdf_bytes(formal_text: str, logo_path: Optional[str] = None) -> bytes:
    if not HAS_REPORTLAB:
        raise RuntimeError("reportlab not installed")
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 60
    # draw logo if file path provided
    if logo_path and os.path.exists(logo_path):
        try:
            img = ImageReader(logo_path)
            c.drawImage(img, 40, height - 70, width=160, height=40, preserveAspectRatio=True)
        except Exception:
            logger.exception("drawImage failed")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "SWIFT ALLIANCE - MESSAGE OUTPUT")
    y -= 24
    c.setFont("Helvetica", 9)
    for line in formal_text.splitlines():
        if y < 60:
            c.showPage()
            y = height - 60
        c.drawString(40, y, line[:120])
        y -= 12
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()

# --- UI: Login (logo on top) ----------------------------------------------------
st.markdown('<div class="header-bar">SWIFT Alliance — Client</div>', unsafe_allow_html=True)

# show logo if configured in config.json or uploaded in repo
logo_path = None
cfg = {}
try:
    with open("config.json", "r", encoding="utf-8") as cf:
        cfg = json.load(cf)
    logo_path = cfg.get("logo_path")
    if logo_path and not os.path.isabs(logo_path):
        logo_path = os.path.join(os.getcwd(), logo_path)
except Exception:
    pass

if logo_path and os.path.exists(logo_path):
    st.image(logo_path, width=240)
else:
    st.info("No logo configured. Upload logo via the UI or add assets/swift_logo.png and config.json")

# login card
if "auth_token" not in st.session_state:
    st.session_state["auth_token"] = None
if "username" not in st.session_state:
    st.session_state["username"] = ""

if not st.session_state["auth_token"]:
    st.subheader("Login")
    col1, col2 = st.columns([2,1])
    with col1:
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
    with col2:
        if st.button("Login"):
            token = auth_login(username, password)
            if token:
                st.session_state["auth_token"] = token
                st.session_state["username"] = username
                st.experimental_rerun()
    st.stop()

# --- Main app after login -------------------------------------------------------
st.sidebar.markdown(f"Logged in as: **{st.session_state.get('username')}**")
st.title("Compose & Submit SWIFT Message")

# demo account selection
create_demo = st.sidebar.button("Create demo accounts")
if create_demo:
    # write a small demo accounts file if desired
    demo_accounts = ["CH970020620625170160K", "UBSWCHZH80A"]
    st.session_state["demo_accounts"] = demo_accounts

accounts = st.session_state.get("demo_accounts", ["CH970020620625170160K","UBSWCHZH80A"])
selected_account = st.selectbox("Select account", ["-- none --"] + accounts)

# sender info
st.subheader("Sender Bank Info")
sender_bic = st.text_input("SENDER BIC", value="UBSWCHZH80A")
sender_name = st.text_input("BANK SENDER", value="UBS SWITZERLAND AG")
sender_address = st.text_input("BANK ADDRESS", value="PARADEPLATZ 6, 8098, ZURICH, SWITZERLAND")
sender_account_name = st.text_input("ACCOUNT NAME", value="ANDRO AG")
sender_iban = st.text_input("ACCOUNT IBAN", value="CH970020620625170160K")

# message composition
st.subheader("Message")
msg_type = st.selectbox("Message Type", ["MT103","MT199","MT700","MT760","MT799","ISO20022"])
ordering_name = st.text_input("Ordering name", value=sender_account_name)
ordering_account = st.text_input("Ordering account (IBAN)", value=sender_iban)
beneficiary_name = st.text_input("Beneficiary name")
beneficiary_account = st.text_input("Beneficiary account (IBAN)")
amount = st.text_input("Amount (e.g., 1234.56)", value="0.00")
currency = st.text_input("Currency", value="CHF")
remittance = st.text_area("Narrative / Remittance", height=120)
reference = st.text_input("Reference", value=str(uuid.uuid4()).upper()[:12])

# DOS boot simulation
if st.button("Start (DOS boot)"):
    dos_lines = [
        "SWIFT Configurator v1.0",
        "Loading X.509 keystore... [SIMULATED]",
        f"Verifying sender BIC: {sender_bic} ... [FOUND]",
        "Negotiating TLS... [OK]",
        "Message validation service: [OK]",
        "Ready. Compose message."
    ]
    block = st.empty()
    out = ""
    for l in dos_lines:
        out += l + "\n"
        block.markdown(f'<div class="dos-box">{out}</div>', unsafe_allow_html=True)
        time.sleep(0.08)
    st.success("Boot finished")

# build message string (simple)
def build_message_text():
    if msg_type.startswith("MT"):
        lines = []
        lines.append(f":20:{reference}")
        if msg_type == "MT103":
            lines.append(f":32A:{datetime.date.today().strftime('%y%m%d')}{currency}{amount}")
            lines.append(f":50K:{ordering_name} /{ordering_account}")
            lines.append(f":59:{beneficiary_name} /{beneficiary_account}")
            lines.append(f":70:{remittance}")
            lines.append(":71A:SHA")
        else:
            # simple free text
            lines.append(f":79:{remittance}")
        return "\n".join(lines)
    else:
        # simple pain.001 fragment
        xml = f"<CstmrCdtTrfInitn><GrpHdr><MsgId>{reference}</MsgId></GrpHdr><PmtInf><CdtTrfTxInf><Amt><InstdAmt Ccy='{currency}'>{amount}</InstdAmt></Amt></CdtTrfTxInf></PmtInf></CstmrCdtTrfInitn>"
        return xml

st.markdown("---")
if st.button("Compose & Submit"):
    message_body = build_message_text()
    start_ts = datetime.datetime.utcnow().isoformat()
    payload = {
        "type": msg_type,
        "reference": reference,
        "ordering_name": ordering_name,
        "ordering_account": ordering_account,
        "beneficiary_name": beneficiary_name,
        "beneficiary_account": beneficiary_account,
        "amount": amount,
        "currency": currency,
        "remittance": remittance,
        "body": message_body,
        "sender": {
            "bic": sender_bic,
            "bank_name": sender_name,
            "bank_address": sender_address,
            "account_name": sender_account_name,
            "account_iban": sender_iban
        }
    }
    if DEMO_MODE:
        # Demo flow: create local "message id", pretend to queue
        mid = f"demo-{uuid.uuid4().hex[:8]}"
        st.session_state["last_message_id"] = mid
        st.session_state["last_message_body"] = message_body
        st.session_state["last_formal"] = build_formal_text(msg_type, message_body, payload["sender"], start_ts, datetime.datetime.utcnow().isoformat(), selected_account or "")
        st.success(f"Message created (demo) id={mid}")
    else:
        try:
            token = st.session_state.get("auth_token")
            res = api_post("/messages/create", token=token, data=payload)
            mid = res.get("message_id") or res.get("id")
            st.session_state["last_message_id"] = mid
            st.success(f"Message submitted, id={mid}")
        except Exception as e:
            st.error(f"Failed to submit message: {e}")
            logger.exception("submit failed")

# Poll status / download
mid = st.session_state.get("last_message_id")
if mid:
    st.markdown("---")
    st.subheader(f"Message ID: {mid}")
    if st.button("Refresh Status"):
        if DEMO_MODE:
            st.info("Demo: status=QUEUED -> SENT")
            st.session_state["last_status"] = "SENT"
        else:
            try:
                token = st.session_state.get("auth_token")
                status = api_get(f"/messages/{mid}/status", token=token)
                st.session_state["last_status"] = status.get("state")
                st.write(status)
            except Exception as e:
                st.error(f"Status check failed: {e}")
    last_status = st.session_state.get("last_status", "UNKNOWN")
    st.write("Status:", last_status)

    if st.button("Download TXT"):
        if DEMO_MODE:
            txt = st.session_state.get("last_formal") or st.session_state.get("last_message_body", "")
            st.download_button("Download TXT", data=txt.encode("utf-8"), file_name=f"swift_msg_{mid}.txt", mime="text/plain")
        else:
            try:
                token = st.session_state.get("auth_token")
                data = api_get(f"/messages/{mid}/download", token=token)
                # server should return bytes or base64; handle bytes
                if isinstance(data, (bytes, bytearray)):
                    st.download_button("Download TXT", data=data, file_name=f"swift_msg_{mid}.txt", mime="text/plain")
                else:
                    # expect JSON with 'txt' or 'pdf' base64
                    txt = data.get("txt") or data.get("content")
                    st.download_button("Download TXT", data=txt.encode("utf-8"), file_name=f"swift_msg_{mid}.txt", mime="text/plain")
            except Exception as e:
                st.error(f"Download failed: {e}")

    if st.button("Download PDF"):
        if DEMO_MODE:
            formal = st.session_state.get("last_formal") or st.session_state.get("last_message_body","")
            try:
                pdf_bytes = generate_pdf_bytes(formal, logo_path)
                st.download_button("Download PDF", data=pdf_bytes, file_name=f"swift_msg_{mid}.pdf", mime="application/pdf")
            except Exception as e:
                st.error("PDF generation failed. Ensure reportlab is installed.")
        else:
            try:
                token = st.session_state.get("auth_token")
                pdf_data = api_get(f"/messages/{mid}/download?format=pdf", token=token)
                # if bytes:
                if isinstance(pdf_data, (bytes, bytearray)):
                    st.download_button("Download PDF", data=pdf_data, file_name=f"swift_msg_{mid}.pdf", mime="application/pdf")
                else:
                    # maybe base64 in JSON
                    b64 = pdf_data.get("pdf_b64")
                    if b64:
                        import base64
                        raw = base64.b64decode(b64)
                        st.download_button("Download PDF", data=raw, file_name=f"swift_msg_{mid}.pdf", mime="application/pdf")
            except Exception as e:
                st.error(f"PDF download failed: {e}")

st.markdown("---")
st.caption("Client runs in demo or integrated mode. On Streamlit Cloud, configure [API] secrets for real microservices.")