"""SmartBill AI — Streamlit frontend for the existing n8n bill-extraction workflow.

Deployment: set N8N_WEBHOOK_URL in Streamlit Community Cloud > App settings > Secrets.
The n8n webhook must reply with actual bill JSON. Google Sheets saving occurs in n8n,
not in this frontend. No bill data is invented on empty or failed responses.
"""

import hashlib
import json
import os
from datetime import datetime
from typing import Any

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="SmartBill AI | Bill Extraction Agent", page_icon="🧾",
    layout="wide", initial_sidebar_state="collapsed",
)

# Keep webhook URLs out of public repositories and out of the app's screen.
def get_webhook_url() -> str:
    url = os.environ.get("N8N_WEBHOOK_URL", "").strip()
    if not url:
        try:
            url = str(st.secrets.get("N8N_WEBHOOK_URL", "")).strip()
        except Exception:  # No secrets file or cloud secrets configured.
            pass
    return url if url.startswith("https://") else ""


WEBHOOK_URL = get_webhook_url()
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
CATEGORIES = ("Groceries", "Electronics", "Fashion", "Food", "Others")
CATEGORY_COLORS = {
    "Groceries": "#3478F6", "Electronics": "#22D3EE",
    "Fashion": "#A855F7", "Food": "#FB923C", "Others": "#34D399",
}

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html,body,[class*="css"],.stApp{font-family:Inter,sans-serif}
.stApp{background:radial-gradient(circle at 80% 0%,#12264a 0%,#080f20 35%,#080d1a 100%);color:#e9f0ff}
[data-testid="stSidebar"]{background:#0d172c;border-right:1px solid #233553}
[data-testid="stHeader"]{background:transparent}
.block-container{padding-top:1.3rem;max-width:1400px}
h1,h2,h3{color:#f3f7ff!important}
.hero{padding:24px 26px;border:1px solid #263b60;border-radius:20px;background:linear-gradient(115deg,#142b50,#101c37 65%,#122b40);margin-bottom:20px}
.eyebrow{color:#51d8ef;font-size:12px;letter-spacing:2px;font-weight:800}
.hero h1{font-size:clamp(25px,4vw,35px);margin:8px 0 10px;line-height:1.15}
.muted{color:#a4b4ce}
.stat{border:1px solid #263957;border-radius:17px;background:#101d35;padding:20px;min-height:110px}
.stat .k{color:#9eafcb;font-size:13px}.stat .v{font-size:26px;font-weight:800;color:#f5f9ff;margin-top:10px}
.panel{border:1px solid #263957;border-radius:18px;background:#101d35;padding:20px;margin:12px 0}
.sectiontitle{font-size:19px;font-weight:750;color:#edf5ff;margin-bottom:12px}
.stButton>button[kind="primary"]{background:linear-gradient(90deg,#00b7db,#5275f5);border:none;color:white;font-weight:700;border-radius:12px}
.stButton>button{border-radius:12px}
[data-testid="stFileUploader"]{border:1px dashed #42648b;border-radius:16px;padding:12px;background:#0b1930}
hr{border-color:#263957}
@media(max-width:720px){.block-container{padding-left:1rem;padding-right:1rem}.hero{padding:19px}.stat{min-height:86px;padding:14px}}
</style>""", unsafe_allow_html=True)

if "smartbill_history" not in st.session_state:
    st.session_state.smartbill_history = []
if "smartbill_results" not in st.session_state:
    st.session_state.smartbill_results = []
if "smartbill_done_hashes" not in st.session_state:
    st.session_state.smartbill_done_hashes = set()
if "smartbill_latest" not in st.session_state:
    st.session_state.smartbill_latest = None


def number(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(str(value).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return 0.0


def money(value: Any) -> str:
    return f"₹{number(value):,.2f}"


def as_object(value: Any) -> dict:
    """Decode common n8n JSON shapes, including JSON encoded as a string."""
    for _ in range(6):
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("No receipt details were returned.")
            try:
                value = json.loads(value)
            except (ValueError, TypeError) as ex:
                raise ValueError("The receipt response was not in the expected format.") from ex
            continue
        if isinstance(value, list):
            if not value:
                raise ValueError("No receipt data was returned.")
            value = value[0]
            continue
        if not isinstance(value, dict):
            raise ValueError("The receipt response had an unexpected format.")
        if value.get("success") is False:
            raise ValueError("Receipt processing failed: " + str(value.get("message") or value.get("error") or "Unknown error")[:160])
        if not isinstance(value.get("bill"), dict) and any(
            k in value for k in ("shop_name", "store_name", "merchant_name", "grand_total", "total_amount", "total", "items", "line_items")
        ):
            return value
        nested = next((value[k] for k in ("bill", "data", "extracted_data", "receipt", "result", "output", "response", "body", "json")
                       if k in value and value[k] is not None), None)
        if nested is None:
            raise ValueError("The response did not include extracted receipt details.")
        # Save acknowledgement lives on the envelope; preserve it for a later check.
        if isinstance(nested, dict):
            # n8n sometimes returns items next to a nested bill object.
            if "items" in value and "items" not in nested:
                nested = {**nested, "items": value["items"]}
            if "saved" in value and "saved" not in nested:
                nested = {**nested, "saved": value["saved"]}
        value = nested
    raise ValueError("The receipt response could not be read.")


def detect_category(bill: dict) -> str:
    aliases = {
        "grocery": "Groceries", "groceries": "Groceries", "supermarket": "Groceries",
        "clothes": "Fashion", "clothing": "Fashion", "fashion": "Fashion", "apparel": "Fashion",
        "electronics": "Electronics", "electronic": "Electronics",
        "food": "Food", "restaurant": "Food", "dining": "Food",
    }
    provided = str(bill.get("category") or "").strip().lower()
    if provided in aliases:
        return aliases[provided]
    desc = " ".join(str(x.get("Product", "")) for x in (bill.get("items") or []) if isinstance(x, dict)).lower()
    shop = str(bill.get("shop_name") or "").lower()
    kw = {
        "Fashion": ("kurti", "shirt", "jeans", "saree", "dress", "boutique", "garments"),
        "Electronics": ("laptop", "mobile", "charger", "headphones", "computer", "tablet"),
        "Groceries": ("rice", "dal", "milk", "eggs", "tomato", "banana", "oil", "flour", "supermarket", "kirana"),
        "Food": ("biryani", "pizza", "burger", "restaurant", "cafe", "dining"),
    }
    for source in (desc, shop):
        scores = {cat: sum(word in source for word in words) for cat, words in kw.items()}
        winner = max(scores, key=scores.get)
        if scores[winner] > 0:
            return winner
    return "Others"


def read_bill(payload: Any) -> dict:
    bill = as_object(payload)
    # Preserve explicit saved acknowledgement if it exists; absence is unknown, not success.
    raw_items = bill.get("items", bill.get("line_items", [])) or []
    if not isinstance(raw_items, list):
        raw_items = []
    items = []
    for i in raw_items:
        if isinstance(i, dict):
            items.append({
                "Product": str(i.get("product_name") or i.get("name") or i.get("Product") or "Unknown"),
                "Qty": number(i.get("quantity", i.get("Qty", 1))),
                "Unit Price (₹)": number(i.get("unit_price", i.get("Unit Price (₹)", 0))),
                "GST (₹)": number(i.get("tax_amount", i.get("GST (₹)", 0))),
                "Line Total (₹)": number(i.get("line_total", i.get("Line Total (₹)", i.get("total", 0)))),
            })
    cleaned = dict(bill)
    cleaned["shop_name"] = str(bill.get("shop_name") or bill.get("store_name") or bill.get("merchant_name") or "Unknown")
    cleaned["bill_date"] = str(bill.get("bill_date") or bill.get("date") or "—")
    cleaned["items"] = items
    cleaned["grand_total"] = number(bill.get("grand_total", bill.get("total_amount", bill.get("total", 0))))
    cleaned["category"] = detect_category(cleaned)
    # Empty n8n response is NOT a successfully extracted bill.
    if cleaned["shop_name"] == "Unknown" and not items and not cleaned["grand_total"]:
        raise ValueError("No usable receipt details were found.")
    return cleaned


class WebhookError(Exception):
    """Human-friendly diagnostic, without exposing private webhook addresses."""


def send_to_n8n(upload) -> tuple[dict, str]:
    if not WEBHOOK_URL:
        raise WebhookError("Bill processing is unavailable because the app connection is not configured.")
    data = upload.getvalue()
    if not data:
        raise WebhookError("This photo is empty. Select a different image.")
    if len(data) > MAX_IMAGE_BYTES:
        raise WebhookError("This image is over 10 MB. Upload a smaller photo.")
    mime = upload.type or "image/jpeg"
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in ALLOWED_MIME:
        raise WebhookError("Please choose a JPG, PNG or WEBP receipt.")
    # A stable submission ID can be used as a deduplication key in Google Sheets/n8n.
    submission_id = hashlib.sha256(data).hexdigest()[:32]
    try:
        response = requests.post(
            WEBHOOK_URL,
            files={"file": (upload.name, data, mime)},
            data={"submission_id": submission_id, "currency": "INR"},
            timeout=(15, 115),
            allow_redirects=False,
        )
    except requests.exceptions.Timeout as ex:
        raise WebhookError("Processing took too long. The receipt may already be saved. Check your saved records before trying again.") from ex
    except requests.exceptions.RequestException as ex:
        raise WebhookError("Could not connect to the receipt processing service. Please try later.") from ex

    if 300 <= response.status_code < 400:
        raise WebhookError(f"The processing service redirected unexpectedly (HTTP {response.status_code}). Please contact the app owner.")
    if not response.ok:
        if response.status_code == 404:
            msg = "The receipt service is unavailable (HTTP 404). Please contact the app owner."
        elif response.status_code in (401, 403):
            msg = f"The receipt service denied access (HTTP {response.status_code}). Please contact the app owner."
        elif response.status_code >= 500:
            msg = f"Receipt processing failed on the server (HTTP {response.status_code}). Check saved records before retrying."
        else:
            msg = f"The processing service rejected the receipt (HTTP {response.status_code})."
        raise WebhookError(msg)

    body = response.content.strip()
    if not body:
        raise WebhookError(
            "Processing returned no receipt details. The receipt might already be saved; "
            "check your saved records before retrying."
        )
    try:
        payload = response.json()
    except ValueError as ex:
        raise WebhookError(
            "Processing returned an unreadable response. Check your saved records "
            "before retrying to prevent duplicate entries."
        ) from ex
    try:
        return read_bill(payload), submission_id
    except ValueError as ex:
        raise WebhookError(f"The response did not contain a valid extracted receipt: {ex}") from ex


with st.sidebar:
    st.markdown("## 🧾 SmartBill AI")
    st.caption("BILL EXTRACTION AGENT")
    st.divider()
    page = st.radio("Navigation", ["Overview", "Upload Bill", "Bill History"], label_visibility="collapsed")
    st.divider()
    st.caption("Secure AI-powered receipt processing")
    st.caption("Bill history here lasts only for the current browser session.")

st.markdown('''<div class="hero"><div class="eyebrow">INTELLIGENT RECEIPT MANAGEMENT</div><h1>🧾 SmartBill AI — Bill Extraction Agent</h1><div class="muted">Upload multiple receipts, extract items with AI, and track spending in Indian Rupees.</div></div>''', unsafe_allow_html=True)

history = st.session_state.smartbill_history

if page == "Overview":
    cols = st.columns(4)
    stats = [
        ("Receipts processed", str(len(history))),
        ("Total spending", money(sum(number(x.get("grand_total")) for x in history))),
        ("Items extracted", str(sum(len(x.get("items", [])) for x in history))),
        ("Currency", "INR ₹"),
    ]
    for c, (name, value) in zip(cols, stats):
        with c:
            st.markdown(f'<div class="stat"><div class="k">{name}</div><div class="v">{value}</div></div>', unsafe_allow_html=True)
    left, right = st.columns([1.15, 1])
    with left:
        st.markdown('<div class="panel"><div class="sectiontitle">📊 Spending by Category</div><div class="muted">Real extracted bills only</div></div>', unsafe_allow_html=True)
        category_totals = {k: 0.0 for k in CATEGORIES}
        for bill in history:
            category_totals[detect_category(bill)] += number(bill.get("grand_total"))
        chart = pd.DataFrame([{"Category": k, "Amount": v} for k, v in category_totals.items() if v > 0])
        if chart.empty:
            st.info("Process your first bill to view the spending circle.")
        else:
            try:
                import plotly.express as px
                fig = px.pie(chart, names="Category", values="Amount", hole=0.67,
                             color="Category", color_discrete_map=CATEGORY_COLORS)
                fig.update_traces(textposition="outside", textinfo="label+percent")
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                  font_color="#dbeafe", margin=dict(t=12, b=12, l=10, r=10),
                                  showlegend=True, legend=dict(orientation="h", y=-0.1), height=350)
                st.plotly_chart(fig, use_container_width=True)
            except ImportError:
                st.bar_chart(chart.set_index("Category"))
    with right:
        st.markdown('<div class="panel"><div class="sectiontitle">✨ How it works</div><div class="muted">1. Select several receipt photos<br><br>2. Extract one receipt at a time automatically<br><br>3. Save the extracted receipt details<br><br>4. View your category breakdown and recent bills</div></div>', unsafe_allow_html=True)
        st.markdown("### Recent bills")
        if history:
            st.dataframe(pd.DataFrame([{"Store": b.get("shop_name", "Unknown"), "Date": b.get("bill_date", "—"), "Total": money(b.get("grand_total")), "Save status": b.get("_sheet_status", "Unconfirmed")} for b in reversed(history[-5:])]), hide_index=True, use_container_width=True)
        else:
            st.info("No bills processed in this browser session yet.")

elif page == "Upload Bill":
    st.markdown("## 📤 Upload shopping bills")
    st.caption("Select multiple JPG, PNG or WEBP photos (up to 10 MB each). Avoid including card or bank details.")
    uploaded_files = st.file_uploader("Choose receipt photos", type=["jpg", "jpeg", "png", "webp"],
                                     accept_multiple_files=True, key="smartbill_uploader")
    if uploaded_files:
        st.info(f"{len(uploaded_files)} image(s) selected — one click will process them all.")
        with st.expander("Receipt previews"):
            columns = st.columns(min(3, len(uploaded_files)))
            for i, upload in enumerate(uploaded_files):
                with columns[i % len(columns)]:
                    st.image(upload, caption=upload.name, use_container_width=True)
        if st.button("✨ Extract & Save All Bills", type="primary", use_container_width=True):
            progress = st.progress(0)
            results = []
            for i, upload in enumerate(uploaded_files, 1):
                progress.progress((i - 1) / len(uploaded_files), text=f"Processing {i}/{len(uploaded_files)}: {upload.name}")
                receipt_hash = hashlib.sha256(upload.getvalue()).hexdigest()
                if receipt_hash in st.session_state.smartbill_done_hashes:
                    results.append({"File": upload.name, "Status": "Already processed (this session)", "Shop": "—", "Total": "—", "Details": "Skipped duplicate to avoid repeat requests"})
                    progress.progress(i / len(uploaded_files))
                    continue
                try:
                    bill, sub_id = send_to_n8n(upload)
                    bill["submission_id"] = sub_id
                    bill["source_filename"] = upload.name
                    bill["_sheet_status"] = ("Save confirmed" if bill.get("saved") is True else
                                              "Reported not saved" if bill.get("saved") is False else "Not confirmed")
                    st.session_state.smartbill_done_hashes.add(receipt_hash)
                    st.session_state.smartbill_history.append(bill)
                    st.session_state.smartbill_latest = bill
                    status = ("Extracted + save confirmed" if bill.get("saved") is True else
                              "Extracted (save failed)" if bill.get("saved") is False else
                              "Extracted (save unconfirmed)")
                    details = ("" if bill.get("saved") is True else
                               "Saving was not confirmed; check saved records" if bill.get("saved") is False else
                               "Saving was not confirmed; check saved records")
                    results.append({"File": upload.name, "Status": status, "Shop": bill["shop_name"], "Total": money(bill["grand_total"]), "Details": details})
                except WebhookError as ex:
                    results.append({"File": upload.name, "Status": "Needs checking", "Shop": "—", "Total": "—", "Details": str(ex)})
                except Exception:
                    results.append({"File": upload.name, "Status": "Needs checking", "Shop": "—", "Total": "—", "Details": "An unexpected error occurred. Check saved records before retrying."})
                progress.progress(i / len(uploaded_files), text=f"Completed {i}/{len(uploaded_files)}")
            st.session_state.smartbill_results = results
        if st.session_state.smartbill_results:
            rows = st.session_state.smartbill_results
            if all(x["Status"].startswith("Extracted") or x["Status"].startswith("Already") for x in rows):
                st.success("Batch processing finished. Check saved records for any unconfirmed entries.")
            else:
                st.warning("Some receipts could not be verified. Check the results and your saved records before retrying to avoid duplicates.")
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if st.session_state.smartbill_latest:
            bill = st.session_state.smartbill_latest
            st.divider()
            st.markdown("## 🧾 Latest extracted receipt")
            a, b, c = st.columns(3)
            a.metric("Store", bill.get("shop_name", "Unknown"))
            b.metric("Date", bill.get("bill_date", "—"))
            c.metric("Total", money(bill.get("grand_total")))
            if bill.get("items"):
                st.dataframe(pd.DataFrame(bill["items"]), hide_index=True, use_container_width=True)
            export = {k: v for k, v in bill.items() if not k.startswith("_")}
            st.download_button("⬇️ Download receipt JSON", json.dumps(export, ensure_ascii=False, indent=2),
                               file_name="smartbill_receipt.json", mime="application/json")
    elif st.session_state.smartbill_results:
        st.info("Select receipt photos to start a new batch.")

else:
    st.markdown("## 📚 Bill History")
    st.caption("This history lasts only for your current browser session. Saved records remain available in your connected storage.")
    if not history:
        st.info("No receipts have been extracted in this session.")
    else:
        frame = pd.DataFrame([{"Shop": b.get("shop_name", "Unknown"), "Date": b.get("bill_date", "—"),
                              "Category": b.get("category", "Others"), "Total (₹)": number(b.get("grand_total")),
                              "Save status": b.get("_sheet_status", "Unconfirmed")} for b in history])
        st.dataframe(frame, hide_index=True, use_container_width=True)
        st.download_button("⬇️ Download history CSV", frame.to_csv(index=False).encode("utf-8-sig"),
                           file_name="smartbill_history.csv", mime="text/csv")

st.divider()
st.caption("SmartBill AI • Receipt Extraction Agent • INR ₹")
