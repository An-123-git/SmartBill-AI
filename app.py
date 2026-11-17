import os
import io
import json
import uuid
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="SmartBill AI — Bill Extraction Agent", page_icon="🧾", layout="wide", initial_sidebar_state="expanded")

WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL", os.getenv("N8N_WEBHOOK_URL", ""))

st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html,body,[class*="css"],.stApp{font-family:Inter,sans-serif}
.stApp{background:radial-gradient(circle at 80% 0%,#12264a 0%,#080f20 35%,#080d1a 100%);color:#e9f0ff}
[data-testid="stSidebar"]{background:#0d172c;border-right:1px solid #233553}
[data-testid="stHeader"]{background:transparent}
.block-container{padding-top:2rem;max-width:1500px}
h1,h2,h3{color:#f3f7ff!important}
.hero{padding:26px 30px;border:1px solid #263b60;border-radius:22px;background:linear-gradient(115deg,#142b50,#101c37 65%,#122b40);margin-bottom:20px}
.eyebrow{color:#51d8ef;font-size:12px;letter-spacing:2px;font-weight:800}
.hero h1{font-size:34px;margin:8px 0 10px;line-height:1.15}
.muted{color:#a4b4ce}
.stat{border:1px solid #263957;border-radius:17px;background:#101d35;padding:20px;min-height:120px}
.stat .k{color:#9eafcb;font-size:13px}.stat .v{font-size:28px;font-weight:800;color:#f5f9ff;margin-top:12px}
.panel{border:1px solid #263957;border-radius:18px;background:#101d35;padding:20px;margin:14px 0}
.sectiontitle{font-size:19px;font-weight:750;color:#edf5ff;margin-bottom:12px}
.stButton>button[kind="primary"]{background:linear-gradient(90deg,#00b7db,#5275f5);border:none;color:white;font-weight:700;border-radius:12px}
.stButton>button{border-radius:12px}
[data-testid="stFileUploader"]{border:1px dashed #42648b;border-radius:16px;padding:12px;background:#0b1930}
[data-testid="stMetric"]{background:#101d35;border:1px solid #263957;border-radius:16px;padding:16px}
[data-testid="stMetricLabel"]{color:#9eafcb}
hr{border-color:#263957}
</style>''', unsafe_allow_html=True)

if "history" not in st.session_state:
    st.session_state.history = []
if "latest" not in st.session_state:
    st.session_state.latest = None


def money(value):
    try:
        return f"₹{float(value or 0):,.2f}"
    except (ValueError, TypeError):
        return "₹0.00"


def unwrap_response(data):
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise ValueError("The n8n response must be a JSON object.")
    if isinstance(data.get("data"), dict):
        data = data["data"]
    if isinstance(data.get("bill"), dict):
        bill = dict(data["bill"])
        bill["items"] = data.get("items", bill.get("items", []))
        return bill
    return data


def read_bill(data):
    bill = unwrap_response(data)
    items = bill.get("items", [])
    if not isinstance(items, list):
        items = []
    clean = []
    for item in items:
        if isinstance(item, dict):
            clean.append({
                "Product": item.get("product_name", item.get("name", "Unknown")),
                "Qty": item.get("quantity", 1),
                "Unit Price (₹)": item.get("unit_price", 0),
                "GST (₹)": item.get("tax_amount", 0),
                "Line Total (₹)": item.get("line_total", item.get("total", 0)),
            })
    return bill, clean


with st.sidebar:
    st.markdown("## 🧾 SmartBill AI")
    st.caption("BILL EXTRACTION AGENT")
    st.divider()
    page = st.radio("Navigation", ["Overview", "Upload Bill", "Bill History"], label_visibility="collapsed")
    st.divider()
    st.markdown("**⚡ Automation Pipeline**")
    st.caption("Streamlit → n8n → AI Vision → Google Sheets")
    if WEBHOOK_URL:
        st.success("n8n URL configured")
    else:
        st.warning("n8n URL not configured")
    st.caption("Use the sidebar settings or environment variable N8N_WEBHOOK_URL.")

st.markdown('''<div class="hero"><div class="eyebrow">INTELLIGENT RECEIPT MANAGEMENT</div><h1>SmartBill AI — Bill Extraction Agent</h1><div class="muted">Upload shopping receipts, extract item-level details with AI, and organize your expenses in Indian Rupees.</div></div>''', unsafe_allow_html=True)

history = st.session_state.history
bill_count = len(history)
total_spend = sum(float(b.get("grand_total") or 0) for b in history)
item_count = sum(len(b.get("items", [])) for b in history)

if page == "Overview":
    c1, c2, c3, c4 = st.columns(4)
    for col, title, value in [(c1, "Receipts processed", str(bill_count)), (c2, "Total spending", money(total_spend)), (c3, "Items extracted", str(item_count)), (c4, "Currency", "INR · ₹")]:
        with col:
            st.markdown(f'<div class="stat"><div class="k">{title}</div><div class="v">{value}</div></div>', unsafe_allow_html=True)
    left, right = st.columns([1.2, 1])
    with left:
        st.markdown('<div class="panel"><div class="sectiontitle">📊 Spending by Category</div><div class="muted">Circular spending breakdown</div></div>', unsafe_allow_html=True)
        categories = {"Groceries": 0.0, "Electronics": 0.0, "Fashion": 0.0, "Food": 0.0, "Others": 0.0}
        for bill in history:
            cat = str(bill.get("category", "Others")).title()
            if cat not in categories:
                cat = "Others"
            categories[cat] += float(bill.get("grand_total") or 0)
        if sum(categories.values()) == 0:
            st.info("Your category circle will fill up after the first bill is processed.")
            # A visual preview, clearly not actual user spending.
            chart = pd.DataFrame({"Category": list(categories), "Amount": [30, 25, 20, 15, 10]})
            st.caption("Illustrative chart — sample proportions, not your spending")
        else:
            chart = pd.DataFrame({"Category": list(categories), "Amount": list(categories.values())})
            chart = chart[chart["Amount"] > 0]
        try:
            import plotly.express as px
            fig = px.pie(chart, names="Category", values="Amount", hole=0.68, color_discrete_sequence=["#22d3ee", "#6c79ff", "#a78bfa", "#34d399", "#fbba69"])
            fig.update_traces(textposition="outside", textinfo="label+percent", marker=dict(line=dict(color="#101d35", width=3)))
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="#dbeafe", margin=dict(t=20,b=20,l=20,r=20), showlegend=True, legend=dict(orientation="h",y=-0.12), height=370, annotations=[dict(text="SPENDING",x=.5,y=.5,font_size=18,showarrow=False,font_color="#c7ddf8")])
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.bar_chart(chart.set_index("Category"))
    with right:
        st.markdown('<div class="panel"><div class="sectiontitle">✨ Smart Receipt Workflow</div><div class="muted">1. Upload a receipt image<br><br>2. AI extracts the shop, date, products, GST and totals<br><br>3. n8n writes the records to Google Sheets<br><br>4. Review your spending in one place</div></div>', unsafe_allow_html=True)
        st.markdown("### 🕘 Recent Bills")
        if history:
            st.dataframe(pd.DataFrame([{"Store": b.get("shop_name", "Unknown"), "Date": b.get("bill_date", "—"), "Total (₹)": b.get("grand_total", 0)} for b in reversed(history[-5:])]), hide_index=True, use_container_width=True)
        else:
            st.info("No bills processed in this session yet.")
        if st.button("➕ Upload your first bill", type="primary", use_container_width=True):
            st.info("Select **Upload Bill** in the sidebar to start.")

elif page == "Upload Bill":
    st.markdown("## 📤 Upload a Shopping Bill")
    st.caption("Supports JPG, PNG and WEBP. Please avoid uploading receipts containing sensitive card details.")
    image_file = st.file_uploader("Choose a bill photo", type=["jpg", "jpeg", "png", "webp"])
    if image_file is not None:
        left, right = st.columns([1, 1])
        with left:
            st.image(image_file, caption="Receipt preview", use_container_width=True)
        with right:
            st.markdown("### 🔎 AI Bill Extraction")
            st.write("When you click Extract, the image is sent to your n8n webhook. The workflow must return extracted JSON and handle Google Sheets saving.")
            if not WEBHOOK_URL:
                st.error("Set N8N_WEBHOOK_URL first. See the setup instructions below the code.")
            if st.button("✨ Extract & Save Bill", type="primary", disabled=not WEBHOOK_URL, use_container_width=True):
                try:
                    image_file.seek(0)
                    with st.spinner("Sending receipt to n8n and waiting for AI extraction..."):
                        response = requests.post(
                            WEBHOOK_URL,
                            files={"file": (image_file.name, image_file.getvalue(), image_file.type or "image/jpeg")},
                            data={"submission_id": str(uuid.uuid4()), "currency": "INR"},
                            timeout=120,
                        )
                        response.raise_for_status()
                        payload = response.json()
                        bill, items = read_bill(payload)
                        if not any(k in bill for k in ("shop_name", "grand_total", "items")):
                            raise ValueError("n8n returned JSON but no recognized bill fields. Check the Respond to Webhook output.")
                        bill["items"] = items
                        st.session_state.latest = bill
                        st.session_state.history.append(bill)
                    st.success("Receipt extracted! Confirm in Google Sheets that the workflow saved it.")
                except requests.exceptions.RequestException as e:
                    st.error(f"Could not reach the n8n webhook: {e}")
                except (ValueError, json.JSONDecodeError) as e:
                    st.error(f"Invalid extraction response: {e}")
        if st.session_state.latest:
            bill = st.session_state.latest
            st.divider()
            st.markdown("## 🧾 Extracted Bill")
            a, b, c = st.columns(3)
            a.metric("Store", str(bill.get("shop_name") or "Unknown"))
            b.metric("Bill Date", str(bill.get("bill_date") or "—"))
            c.metric("Grand Total", money(bill.get("grand_total")))
            st.write(f"**Subtotal:** {money(bill.get('subtotal'))} · **GST:** {money(bill.get('tax_total'))} · **Discount:** {money(bill.get('discount'))}")
            if bill.get("items"):
                st.dataframe(pd.DataFrame(bill["items"]), use_container_width=True, hide_index=True)
            st.download_button("⬇️ Download extracted JSON", json.dumps(bill, indent=2, ensure_ascii=False), file_name="smartbill_extracted.json", mime="application/json")

else:
    st.markdown("## 📚 Bill History")
    st.caption("This is the current Streamlit session only. Google Sheets is the permanent database once connected in n8n.")
    if not history:
        st.info("No bills yet. Upload your first receipt.")
    else:
        frame = pd.DataFrame([{"Shop": b.get("shop_name", "Unknown"), "Date": b.get("bill_date", "—"), "Category": b.get("category", "Others"), "Grand Total (₹)": b.get("grand_total", 0)} for b in history])
        st.dataframe(frame, hide_index=True, use_container_width=True)
        st.download_button("⬇️ Export this session as CSV", frame.to_csv(index=False).encode("utf-8-sig"), "smartbill_history.csv", "text/csv")

st.divider()
st.caption("SmartBill AI • Bill Extraction Agent • Built with Streamlit + n8n + Google Sheets • INR ₹")
