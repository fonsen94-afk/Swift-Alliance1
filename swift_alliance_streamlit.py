"""
Swift Alliance — Single-file Streamlit app (Oracle-styled login, logo on login,
black DOS boot screen, SVG->PNG logo conversion if available, PDF/TXT export).

Usage:
  pip install -r requirements.txt
  streamlit run swift_alliance_streamlit.py

Notes:
 - cairosvg is optional but recommended to reliably embed SVG logos in PDFs.
 - reportlab + pillow required for PDF export.
 - Do NOT embed production SWIFT credentials here. Use st.secrets for real credentials.
"""
import os
import io
import json
import time
import uuid
import shutil
import logging
import hashlib
import requests
import datetime
import random
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple

import streamlit as st
import xml.etree.ElementTree as ET

# Optional libs
try:
    import xmlschema
    HAS_XMLSCHEMA = True
except Exception:
    xmlschema = None
    HAS_XMLSCHEMA = False

try:
    import paramiko
    HAS_PARAMIKO = True
except Exception:
    HAS_PARAMIKO = False

# PDF libs
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    HAS_REPORTLAB = True
except Exception:
    HAS_REPORTLAB = False

# Pillow for image handling
try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

# cairosvg for SVG -> PNG conversion
try:
    import cairosvg
    HAS_CAIROSVG = True
except Exception:
    HAS_CAIROSVG = False

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("swift_alliance_streamlit")

# --- Paths / storage -------------------------------------------------------------
ROOT_DIR = os.path.dirname(__file__)
DATA_FILE = os.path.join(ROOT_DIR, "bank_data.json")
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
os.makedirs(ASSETS_DIR, exist_ok=True)
SCHEMAS_DIR = os.path.join(ASSETS_DIR, "schemas")
os.makedirs(SCHEMAS_DIR, exist_ok=True)
USERS_FILE = os.path.join(ROOT_DIR, "users.json")
CONFIG_FILE = os.path.join(ROOT_DIR, "config.json")

# --- Helpers --------------------------------------------------------------------

def save_config(data: Dict):
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg.update(data)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def load_config() -> Dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

# Password hashing
def hash_password(password: str) -> str:
    salt = "swift_alliance_app_salt_2025"
    return hashlib.sha256((password + salt).encode()).hexdigest()

# Ensure default admin user
def ensure_default_user():
    if not os.path.exists(USERS_FILE):
        admin = {"username": "admin", "password": hash_password("admin")}
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"users": [admin]}, f, indent=2)
ensure_default_user()

def validate_user(username: str, password: str) -> bool:
    if not os.path.exists(USERS_FILE):
        return False
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    for u in data.get("users", []):
        if u.get("username") == username and u.get("password") == hash_password(password):
            return True
    return False

def add_user(username: str, password: str):
    data = {"users": []}
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    data.setdefault("users", []).append({"username": username, "password": hash_password(password)})
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# Simple bank persistence
def load_bank_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"customers": [], "accounts": [], "transactions": {}}

def save_bank_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

BANK = load_bank_data()

def create_demo_customer_and_accounts():
    if BANK.get("customers"):
        return
    cust_id = "CUST" + datetime.datetime.now().strftime("%Y%m%d") + "1001"
    customer = {
        "customer_id": cust_id,
        "first_name": "Andro",
        "last_name": "AG",
        "email": "andro@example.com",
        "phone": "+41 44 000 0000",
        "address": "Paradeplatz 6, 8001 Zurich",
        "date_of_birth": "1970-01-01",
        "id_number": "ID123456",
        "id_type": "Company",
        "created_date": datetime.date.today().isoformat()
    }
    BANK["customers"].append(customer)
    acct1 = {
        "account_number": "CH970020620625170160K",
        "customer_id": cust_id,
        "account_type": "CORPORATE",
        "currency": "CHF",
        "balance": "100000.00"
    }
    acct2 = {
        "account_number": "UBSWCHZH80A",
        "customer_id": cust_id,
        "account_type": "CURRENT",
        "currency": "CHF",
        "balance": "50000.00"
    }
    BANK["accounts"].extend([acct1, acct2])
    save_bank_data(BANK)

# --- SWIFT templates ------------------------------------------------------------
SWIFT_SENDER_INFO_DEFAULT = {
    "bic": "UBSWCHZH80A",
    "bank_name": "UBS SWITZERLAND AG",
    "bank_address": "PARADEPLATZ 6, 8098, ZURICH, SWITZERLAND",
    "account_name": "ANDRO AG / FOR FURTHER FORSAN FOR FRUITS AND VEGETABLES EAST",
    "account_iban": "CH970020620625170160K"
}

MT_TEMPLATES = {
    "MT103": {"required_tags": [":20:", ":32A:", ":50K:", ":59:", ":71A:"], "example": "Classic single-customer credit transfer (MT103)."},
    "MT199": {"required_tags": [":20:", ":21:", ":79:"], "example": "Free-format message (MT199)."},
    "MT700": {"required_tags": [":20:", ":40A:", ":31D:", ":50:", ":59:", ":44A:", ":77B:"], "example": "Issue of a documentary credit (MT700)."},
    "MT760": {"required_tags": [":20:", ":21:", ":32B:", ":50:", ":59:", ":77U:"], "example": "Guarantee or standby (MT760)."},
    "MT799": {"required_tags": [":20:", ":21:", ":79:"], "example": "Free-format pre-advice or reservation (MT799)."}
}

def build_mt_message(msg_type: str, fields: Dict[str, str], sender_info: Dict[str,str], reference: Optional[str]=None) -> str:
    ref = reference or (msg_type[:3] + datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S"))
    lines = []
    lines.append(f"{{1:F01{sender_info.get('bic','UNKNOWN')}XXXX0000000000}}")
    lines.append(f"{{2:O{msg_type}1200{sender_info.get('bic','UNKNOWN')}XXXX0000000000}}")
    lines.append("{4:")
    lines.append(f":20:{ref}")
    for k, v in fields.items():
        lines.append(f"{k}{v}")
    lines.append(":86:Sender Details")
    lines.append(f" /BANK/{sender_info.get('bank_name')}")
    lines.append(f" /ADDR/{sender_info.get('bank_address')}")
    lines.append(f" /ACCTNAME/{sender_info.get('account_name')}")
    lines.append(f" /ACCTIBAN/{sender_info.get('account_iban')}")
    lines.append("-}")
    return "\n".join(lines)

# --- ISO20022 generator & helpers -----------------------------------------------
def format_decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), 'f')

def generate_pain001_xml(payment: Dict) -> str:
    NS = 'urn:iso:std:iso:20022:tech:xsd:pain.001.001.03'
    CstmrCdtTrfInitn = ET.Element('CstmrCdtTrfInitn', xmlns=NS)
    GrpHdr = ET.SubElement(CstmrCdtTrfInitn, 'GrpHdr')
    ET.SubElement(GrpHdr, 'MsgId').text = payment.get('reference', str(uuid.uuid4()))
    ET.SubElement(GrpHdr, 'CreDtTm').text = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
    ET.SubElement(GrpHdr, 'NbOfTxs').text = "1"
    ET.SubElement(GrpHdr, 'CtrlSum').text = format_decimal(payment['amount'])
    PmtInf = ET.SubElement(CstmrCdtTrfInitn, 'PmtInf')
    ET.SubElement(PmtInf, 'PmtInfId').text = "PMT-" + datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    ET.SubElement(PmtInf, 'PmtMtd').text = "TRF"
    ET.SubElement(PmtInf, 'NbOfTxs').text = "1"
    ET.SubElement(PmtInf, 'CtrlSum').text = format_decimal(payment['amount'])
    CdtTrfTxInf = ET.SubElement(PmtInf, 'CdtTrfTxInf')
    PmtId = ET.SubElement(CdtTrfTxInf, 'PmtId')
    ET.SubElement(PmtId, 'EndToEndId').text = payment.get('reference', str(uuid.uuid4()))
    Amt = ET.SubElement(CdtTrfTxInf, 'Amt')
    InstdAmt = ET.SubElement(Amt, 'InstdAmt', Ccy=payment.get('currency','USD'))
    InstdAmt.text = format_decimal(payment['amount'])
    Cdtr = ET.SubElement(CdtTrfTxInf, 'Cdtr')
    ET.SubElement(Cdtr, 'Nm').text = payment.get('beneficiary_name','')
    CdtrAcct = ET.SubElement(CdtTrfTxInf, 'CdtrAcct')
    Id = ET.SubElement(CdtrAcct, 'Id')
    ET.SubElement(Id, 'IBAN').text = payment.get('beneficiary_account','')
    Dbtr = ET.SubElement(CdtTrfTxInf, 'Dbtr')
    ET.SubElement(Dbtr, 'Nm').text = payment.get('ordering_name','')
    DbtrAcct = ET.SubElement(CdtTrfTxInf, 'DbtrAcct')
    DbtrId = ET.SubElement(DbtrAcct, 'Id')
    ET.SubElement(DbtrId, 'IBAN').text = payment.get('ordering_account','')
    if payment.get('remittance_info'):
        RmtInf = ET.SubElement(CdtTrfTxInf, 'RmtInf')
        ET.SubElement(RmtInf, 'Ustrd').text = payment.get('remittance_info')
    xml_bytes = ET.tostring(CstmrCdtTrfInitn, encoding='utf-8')
    import xml.dom.minidom
    dom = xml.dom.minidom.parseString(xml_bytes)
    return dom.toprettyxml(indent="  ")

# --- Logo download & conversion --------------------------------------------------
def _choose_extension(url: str, content_type: str) -> str:
    content_type = (content_type or "").lower()
    if "svg" in content_type or url.lower().endswith(".svg"):
        return ".svg"
    if "png" in content_type or url.lower().endswith(".png"):
        return ".png"
    if "jpeg" in content_type or "jpg" in content_type or url.lower().endswith(".jpg") or url.lower().endswith(".jpeg"):
        return ".jpg"
    return ".png"

def download_logo_from_url(url: str) -> Optional[str]:
    """
    Download logo to assets/; if SVG and cairosvg present, convert to PNG for PDF embedding.
    Returns path to image file that should be used for display and PDF.
    """
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        ext = _choose_extension(url, r.headers.get("content-type",""))
        raw_path = os.path.join(ASSETS_DIR, "swift_logo" + ext)
        with open(raw_path, "wb") as f:
            f.write(r.content)
        # If SVG and cairosvg available, convert to PNG for PDF consumption
        if ext == ".svg":
            if HAS_CAIROSVG:
                png_path = os.path.join(ASSETS_DIR, "swift_logo_converted.png")
                try:
                    cairosvg.svg2png(bytestring=r.content, write_to=png_path, output_width=1024)
                    save_config({"logo_path": png_path})
                    return png_path
                except Exception:
                    logger.exception("SVG -> PNG conversion failed; falling back to raw SVG")
                    save_config({"logo_path": raw_path})
                    return raw_path
            else:
                save_config({"logo_path": raw_path})
                return raw_path
        else:
            save_config({"logo_path": raw_path})
            return raw_path
    except Exception as e:
        logger.exception("Logo download failed: %s", e)
        return None

# --- DOS-like boot (black background, accurate-looking SWIFT steps) -------------
def show_dos_boot(config_sequence: List[str], line_delay: float = 0.06):
    """
    Render a DOS-like black screen showing a SWIFT configuration sequence.
    This is a simulation for UI/demo only.
    """
    block = st.empty()
    out_lines: List[str] = []
    for step in config_sequence:
        out_lines.append(step)
        html = '<div style="background:#000;padding:12px;border-radius:6px"><pre style="color:#00FF70;margin:0;font-family:Courier New, monospace;">' + "\n".join(out_lines) + "</pre></div>"
        block.markdown(html, unsafe_allow_html=True)
        time.sleep(line_delay)

# --- PDF generation -------------------------------------------------------------
def build_formal_output(message_type: str, message_body: str, sender_info: Dict[str,str], start_ts: str, end_ts: str, account_number: str) -> str:
    header_lines = [
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
        f"Selected Account (from system): {account_number}",
        "",
        "MESSAGE TEXT",
        message_body,
        "",
        "MESSAGE HAS BEEN TRANSMITTED SUCCESSFULLY",
        "CONFIRMED & RECEIVED",
        "",
        f"Start Time: {start_ts}",
        f"End Time:   {end_ts}"
    ]
    return "\n".join(header_lines)

def generate_pdf_bytes(formal_text: str, logo_path: Optional[str]=None) -> bytes:
    if not HAS_REPORTLAB:
        raise RuntimeError("reportlab not installed; cannot generate PDF. Install with 'pip install reportlab'.")
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 60
    # Draw logo if available and a raster format
    if logo_path and os.path.exists(logo_path):
        try:
            # If SVG present and not converted, try to convert using cairosvg on the fly
            if logo_path.lower().endswith(".svg") and HAS_CAIROSVG:
                tmp_png = os.path.join(ASSETS_DIR, f"tmp_logo_{uuid.uuid4().hex}.png")
                try:
                    cairosvg.svg2png(url=logo_path, write_to=tmp_png, output_width=1024)
                    img = ImageReader(tmp_png)
                    os.remove(tmp_png)
                except Exception:
                    img = ImageReader(logo_path)  # may fail
            else:
                img = ImageReader(logo_path)
            img_w = min(240, width * 0.4)
            img_h = 50
            c.drawImage(img, 40, height - img_h - 20, width=img_w, height=img_h, preserveAspectRatio=True)
        except Exception:
            logger.exception("Failed to draw logo on PDF")
    # Title
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "SWIFT ALLIANCE - MESSAGE OUTPUT")
    y -= 30
    c.setFont("Helvetica", 10)
    lines = formal_text.splitlines()
    for line in lines:
        if y < 60:
            c.showPage()
            y = height - 60
        max_chars = 95
        while len(line) > max_chars:
            piece = line[:max_chars]
            c.drawString(40, y, piece)
            y -= 14
            line = line[max_chars:]
        c.drawString(40, y, line)
        y -= 14
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()

# --- Styles (Oracle-like) -------------------------------------------------------
_ORACLE_RED = "#D00000"
_STYLES = f"""
<style>
.header-bar {{
  background: linear-gradient(90deg, {_ORACLE_RED}, #b00000);
  color: white;
  padding: 10px 16px;
  border-radius: 6px;
  margin-bottom: 12px;
  font-weight:600;
}}
.login-logo {{
  display:block; margin-left:auto; margin-right:auto; max-width:260px; height:auto; margin-bottom:8px;
}}
.small-muted {{ color:#6b6b6b; font-size:12px; }}
</style>
"""
st.markdown(_STYLES, unsafe_allow_html=True)

# --- Streamlit UI ---------------------------------------------------------------
st.set_page_config(page_title="Swift Alliance - Converter & PDF Export", layout="wide")
st.title("Swift Alliance — SWIFT Message Composer (with PDF/TXT export)")

# --- Login UI (logo on top, Oracle-like style) ---------------------------------
st.markdown('<div class="header-bar">SWIFT Alliance — Secure Composer</div>', unsafe_allow_html=True)

# Load stored config logo path if present
cfg = load_config()
if "logo_path" in cfg and cfg["logo_path"]:
    lp = cfg["logo_path"]
    if not os.path.isabs(lp):
        lp = os.path.join(ROOT_DIR, lp)
    if os.path.exists(lp):
        st.session_state["logo_path"] = lp

logo_show_path = st.session_state.get("logo_path")
if logo_show_path and os.path.exists(logo_show_path):
    st.markdown(f'<img src="file://{os.path.abspath(logo_show_path)}" class="login-logo" />', unsafe_allow_html=True)
else:
    st.markdown('<div style="text-align:center;color:#444;margin-bottom:8px">[Swift logo not set — use the download button below]</div>', unsafe_allow_html=True)

# Login form (robust with safe rerun)
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "username" not in st.session_state:
    st.session_state["username"] = ""

if not st.session_state["logged_in"]:
    st.markdown("### Login")
    col1, col2 = st.columns([2, 1])
    with col1:
        input_uname = st.text_input("Username")
        input_pwd = st.text_input("Password", type="password")
    with col2:
        st.write("")  # spacer
        if st.button("Login", key="login_btn"):
            if validate_user(input_uname, input_pwd):
                st.session_state["username"] = input_uname
                st.session_state["logged_in"] = True
                st.success("Login successful")
                try:
                    if hasattr(st, "experimental_rerun"):
                        st.experimental_rerun()
                except Exception as _ex:
                    logger.exception("st.experimental_rerun() failed: %s", _ex)
            else:
                st.error("Invalid credentials")
        if st.button("Register", key="register_btn"):
            if not input_uname or not input_pwd:
                st.error("Enter username and password")
            else:
                add_user(input_uname, input_pwd)
                st.session_state["username"] = input_uname
                st.session_state["logged_in"] = True
                st.success("User created and logged in")
                try:
                    if hasattr(st, "experimental_rerun"):
                        st.experimental_rerun()
                except Exception as _ex:
                    logger.exception("st.experimental_rerun() failed after register: %s", _ex)
    st.markdown('<div class="small-muted">Register to create a new user. Default admin/admin</div>', unsafe_allow_html=True)
    st.stop()

# After login
uname = st.session_state.get("username", "")
st.sidebar.markdown(f"Logged in as: **{uname}**")

# Demo data
create_demo_customer_and_accounts()

# Account selection
acct_options = [a["account_number"] for a in BANK.get("accounts", [])]
selected_account = st.selectbox("Select account number", ["-- none --"] + acct_options)

# Sender info
st.subheader("Sender Bank Information (editable)")
sender_bic = st.text_input("SENDER BIC", value=SWIFT_SENDER_INFO_DEFAULT["bic"])
sender_bank = st.text_input("BANK SENDER", value=SWIFT_SENDER_INFO_DEFAULT["bank_name"])
sender_addr = st.text_input("BANK ADDRESS", value=SWIFT_SENDER_INFO_DEFAULT["bank_address"])
sender_acct_name = st.text_input("ACCOUNT NAME", value=SWIFT_SENDER_INFO_DEFAULT["account_name"])
sender_iban = st.text_input("ACCOUNT IBAN", value=SWIFT_SENDER_INFO_DEFAULT["account_iban"])

# Logo download UI
st.subheader("Logo")
logo_url = st.text_input("Logo URL (optional)", value="https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/SWIFT_2021_logo.svg/2051px-SWIFT_2021_logo.svg.png")
if st.button("Download & use logo from URL"):
    lp = download_logo_from_url(logo_url)
    if lp:
        st.success(f"Logo downloaded to {lp}")
        st.session_state["logo_path"] = lp
    else:
        st.error("Failed to download logo. Check URL and network.")

logo_path = st.session_state.get("logo_path")
if logo_path and os.path.exists(logo_path):
    try:
        st.image(logo_path, width=260)
    except Exception:
        st.write(f"Logo available at {logo_path}")

# Message type & fields
st.subheader("Message Type & Content")
msg_type = st.selectbox("Select SWIFT message type", list(MT_TEMPLATES.keys()))
ordering_name = st.text_input("Ordering Name", value=sender_acct_name)
ordering_account = st.text_input("Ordering Account (IBAN)", value=sender_iban)
beneficiary_name = st.text_input("Beneficiary Name")
beneficiary_account = st.text_input("Beneficiary Account (IBAN)")
amount_text = st.text_input("Amount (e.g., 1234.56)")
currency = st.text_input("Currency", value="USD")
remittance = st.text_area("Remittance / Narrative", height=120)
reference = st.text_input("Reference (optional)", value=str(uuid.uuid4()).upper()[:12])

st.markdown("MT Fields (key -> value). Example line: :59:Beneficiary /IBAN...")
mt_fields_raw = st.text_area("Enter MT tag lines (one per line)", height=120)
mt_fields = {}
for line in mt_fields_raw.splitlines():
    line = line.strip()
    if not line:
        continue
    if line.startswith(":") and ":" in line[1:]:
        tag, val = line.split(":", 1)
        mt_fields[tag + ":"] = val.strip()

# timestamps & controls
if "message_start_ts" not in st.session_state:
    st.session_state["message_start_ts"] = None
if "message_end_ts" not in st.session_state:
    st.session_state["message_end_ts"] = None

col_a, col_b = st.columns(2)
start_btn = col_a.button("Start Message (DOS boot)")
compose_btn = col_b.button("Compose & Preview Message")
download_pdf_btn = col_b.button("Download PDF")
download_txt_btn = col_b.button("Download TXT")

# DOS sequence (accurate-looking simulation)
if start_btn:
    dos_lines = [
        "SWIFT Configurator v1.0",
        "----------------------------------------",
        "Loading security provider: OpenSSL [OK]",
        "Loading X.509 key store: keys/swift_keystore.p12 [SIMULATED]",
        "Validating local certificate chain... [OK]",
        "Checking certificate validity: NotBefore=2025-01-01 NotAfter=2026-01-01 [OK]",
        "Local certificate fingerprint (SHA256): 3A:5C:...:F2 (simulated)",
        "Loading BIC directory (local copy): bic_directory.csv [OK]",
        f"Verifying sender BIC: {sender_bic} ... [FOUND]",
        "Verifying receiver BIC index... [OK]",
        "Setting messaging profile: FIN MT (MT103/MT199/MT700/MT760/MT799) [OK]",
        "Negotiating TLS protocol: TLS1.2 [OK]",
        "Enabling message validation (XSDs present) [OK]",
        "Loading message encodings: ASCII / UTF-8 [OK]",
        "Performing test connect to gateway (simulated) ... [SUCCESS]",
        "Run-time policy check: message size limits = 20000 bytes",
        "Ready: SWIFT composer available (demo mode)",
        "----------------------------------------",
        "Press Compose to build the message"
    ]
    show_dos_boot(dos_lines, line_delay=0.08)
    st.session_state["message_start_ts"] = datetime.datetime.utcnow().isoformat()
    st.success("Boot sequence complete. You may compose the message now.")

# Compose & Preview
if compose_btn:
    if not st.session_state.get("message_start_ts"):
        st.session_state["message_start_ts"] = datetime.datetime.utcnow().isoformat()
    try:
        amount = Decimal(amount_text.strip())
    except Exception:
        st.error("Invalid amount. Use numeric format e.g., 1234.56")
        st.stop()

    sender_info = {
        "bic": sender_bic,
        "bank_name": sender_bank,
        "bank_address": sender_addr,
        "account_name": sender_acct_name,
        "account_iban": sender_iban
    }
    if msg_type == "MT103":
        fields_map = {
            ":32A:": f"{datetime.date.today().strftime('%y%m%d')}{currency}{format_decimal(amount)}",
            ":50K:": f"{ordering_name} /{ordering_account}",
            ":59:": f"{beneficiary_name} /{beneficiary_account}",
            ":70:": remittance
        }
        fields_map.update(mt_fields)
        message_body = build_mt_message("103", fields_map, sender_info, reference)
    elif msg_type in ("MT199", "MT799"):
        fields_map = mt_fields or {":79:": remittance}
        message_body = build_mt_message(msg_type.replace("MT",""), fields_map, sender_info, reference)
    elif msg_type == "MT700":
        fields_map = mt_fields or {":77B:": remittance}
        message_body = build_mt_message("700", fields_map, sender_info, reference)
    elif msg_type == "MT760":
        fields_map = mt_fields or {":77U:": remittance}
        message_body = build_mt_message("760", fields_map, sender_info, reference)
    else:
        payment = {
            "ordering_account": ordering_account,
            "ordering_name": ordering_name,
            "beneficiary_account": beneficiary_account,
            "beneficiary_name": beneficiary_name,
            "amount": amount,
            "currency": currency,
            "value_date": datetime.date.today().isoformat(),
            "remittance_info": remittance,
            "reference": reference
        }
        try:
            message_body = generate_pain001_xml(payment)
        except Exception as e:
            st.error(f"Failed to build pain.001 XML: {e}")
            st.stop()

    st.session_state["message_end_ts"] = datetime.datetime.utcnow().isoformat()
    formal_text = build_formal_output(msg_type, message_body, sender_info, st.session_state["message_start_ts"], st.session_state["message_end_ts"], selected_account or "")
    st.subheader("Formal Output Preview")
    st.code(formal_text, language="text")

    st.session_state["preview"] = formal_text
    st.session_state["formal_text"] = formal_text

# Download PDF
if download_pdf_btn:
    if not st.session_state.get("formal_text") and not st.session_state.get("preview"):
        st.info("Generate/compose the message first (press Compose & Preview).")
    else:
        formal_text_to_export = st.session_state.get("formal_text") or st.session_state.get("preview")
        st.session_state["message_end_ts"] = datetime.datetime.utcnow().isoformat()
        try:
            pdf_bytes = generate_pdf_bytes(formal_text_to_export, st.session_state.get("logo_path"))
            st.download_button("Download PDF", data=pdf_bytes, file_name=f"swift_message_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}.pdf", mime="application/pdf")
            st.success("PDF generated (download button above).")
        except Exception as e:
            st.error(f"PDF generation failed: {e}. Install reportlab, pillow, and optionally cairosvg for SVG support.")

# Download TXT
if download_txt_btn:
    if not st.session_state.get("formal_text") and not st.session_state.get("preview"):
        st.info("Generate/compose the message first (press Compose & Preview).")
    else:
        txt = st.session_state.get("formal_text") or st.session_state.get("preview") or ""
        st.session_state["message_end_ts"] = datetime.datetime.utcnow().isoformat()
        st.download_button("Download TXT", data=txt.encode("utf-8"), file_name=f"swift_message_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}.txt", mime="text/plain")
        st.success("TXT prepared for download.")

# Last operations
if st.session_state.get("preview"):
    st.markdown("---")
    st.write("Last operations:")
    st.write(f"Start: {st.session_state.get('message_start_ts')}")
    st.write(f"End:   {st.session_state.get('message_end_ts')}")

st.markdown("----")
st.caption("This app generates SWIFT-style messages for demo/export only and does not connect to the SWIFT network.")