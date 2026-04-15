import streamlit as st
import requests
import pandas as pd
import json

st.set_page_config(page_title="Screening Results", page_icon="📊", layout="wide")
st.title("📊 Screening Results")
st.markdown("AI-ranked candidates with semantic analysis and explainable scores.")

API = "http://localhost:8000/api/v1"

# --- Sidebar filters ---
st.sidebar.header("Filters")

try:
    jobs = requests.get(f"{API}/jobs/", timeout=5).json()
    job_options = {f"{j['title']} — {j.get('department','')}": j['id'] for j in jobs}
except Exception:
    job_options = {}

if not job_options:
    st.warning("No jobs found. Please post a job and upload CVs.")
    st.stop()

selected_job_label = st.sidebar.selectbox("Job Position", list(job_options.keys()))
selected_job_id    = job_options[selected_job_label]
min_score          = st.sidebar.slider("Minimum Score", 0.0, 1.0, 0.3, 0.05)
show_limit         = st.sidebar.slider("Show top N candidates", 10, 200, 50)
filter_decision    = st.sidebar.multiselect(
    "Filter by Decision",
    ["needs_review", "hr_approved", "hr_hold", "hr_rejected", "forwarded"],
    default=["needs_review", "hr_approved", "hr_hold"],
)

# Auto-refresh toggle
auto_refresh = st.sidebar.checkbox("Auto-refresh every 30s", value=False)
if auto_refresh:
    import time
    time.sleep(30)
    st.rerun()

# --- Fetch results ---
try:
    results = requests.get(
        f"{API}/screenings/results/{selected_job_id}",
        params={"min_score": min_score, "limit": show_limit},
        timeout=10,
    ).json()
except Exception as e:
    st.error(f"Could not load results: {e}")
    st.stop()

# Filter by decision
if filter_decision:
    results = [r for r in results if r.get("decision") in filter_decision]

# --- Pipeline stats ---
try:
    pipeline = requests.get(f"{API}/jobs/{selected_job_id}/pipeline", timeout=5).json()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Screened",    pipeline.get("total_screened", 0))
    c2.metric("Shortlisted",       pipeline.get("shortlisted", 0),     delta_color="normal")
    c3.metric("Pending Review",    pipeline.get("pending_review", 0))
    c4.metric("Rejected",          pipeline.get("rejected", 0),        delta_color="inverse")
    c5.metric("Avg Score",         f"{pipeline.get('avg_score', 0):.2f}")
except Exception:
    pass

st.markdown("---")

if not results:
    st.info("No results yet. Upload CVs and wait for AI screening to complete.")
    st.stop()

# --- Summary table ---
st.subheader(f"Ranked Candidates ({len(results)} shown)")

table_data = []
for i, r in enumerate(results, 1):
    score = r["composite_score"]
    score_label = (
        "🟢 Strong" if score >= 0.65
        else "🟡 Moderate" if score >= 0.45
        else "🔴 Weak"
    )
    table_data.append({
        "Rank":          i,
        "Name":          r.get("full_name") or "Unknown",
        "Score":         f"{score:.2f} ({score_label})",
        "Semantic Sim":  f"{r.get('semantic_similarity', 0):.2f}",
        "Relevance":     f"{r.get('relevance_score', 0):.2f}",
        "Potential":     f"{r.get('potential_score', 0):.2f}",
        "Anomalies":     r.get("anomaly_count", 0),
        "Returning":     "🔁 Yes" if r.get("is_returning") else "New",
        "Decision":      r.get("decision", "").replace("_", " ").title(),
        "screened_at":   r.get("screened_at", "")[:10] if r.get("screened_at") else "",
    })

df = pd.DataFrame(table_data)
st.dataframe(df, use_container_width=True, hide_index=True)

st.markdown("---")
st.subheader("🔍 Candidate Details")
st.caption("Click on a candidate below to review AI analysis and take action.")

# --- Candidate detail cards ---
for rank, r in enumerate(results, 1):
    score = r["composite_score"]
    score_color = "score-high" if score >= 0.65 else "score-mid" if score >= 0.45 else "score-low"
    anomaly_count = r.get("anomaly_count", 0)
    is_returning  = r.get("is_returning", False)

    header_label = f"#{rank} — {r.get('full_name','Unknown')} | Score: {score:.2f}"
    if is_returning:
        header_label += " 🔁 (Returning)"
    if anomaly_count > 0:
        header_label += f" ⚠️ ({anomaly_count} flags)"

    with st.expander(header_label):
        col1, col2 = st.columns([2, 1])

        with col1:
            # Score breakdown
            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("Composite",  f"{r.get('composite_score', 0):.2f}")
            sc2.metric("Similarity", f"{r.get('semantic_similarity', 0):.2f}")
            sc3.metric("Relevance",  f"{r.get('relevance_score', 0):.2f}")
            sc4.metric("Potential",  f"{r.get('potential_score', 0):.2f}")

            # AI Rationale
            st.markdown("**📖 AI Analysis (for HR review)**")
            st.markdown(
                f'<div class="rationale-box">{r.get("llm_rationale","No analysis available.")}</div>',
                unsafe_allow_html=True
            )

            # Strengths & Gaps
            scol1, scol2 = st.columns(2)
            with scol1:
                st.markdown("**✅ Strengths**")
                strengths = r.get("strengths") or []
                if isinstance(strengths, str):
                    try: strengths = json.loads(strengths)
                    except: strengths = [strengths]
                for s in strengths:
                    st.markdown(f"• {s}")

            with scol2:
                st.markdown("**🔍 Gaps to Explore in Interview**")
                gaps = r.get("gaps") or []
                if isinstance(gaps, str):
                    try: gaps = json.loads(gaps)
                    except: gaps = [gaps]
                for g in gaps:
                    st.markdown(f"• {g}")

            # Transferable skills
            trans = r.get("transferable_skills") or []
            if isinstance(trans, str):
                try: trans = json.loads(trans)
                except: trans = [trans]
            if trans:
                st.markdown("**🔄 Transferable Skills Identified**")
                st.markdown(", ".join(f"`{t}`" for t in trans))

            # Value-add insights
            vai = r.get("value_add_insights") or []
            if isinstance(vai, str):
                try: vai = json.loads(vai)
                except: vai = [vai]
            if vai:
                st.markdown("**💡 Talent Development Insights**")
                for insight in vai:
                    st.markdown(f"• {insight}")

        with col2:
            # CV download link
            st.markdown("**📄 CV File**")
            st.markdown(f"`{r.get('original_filename','')}`")
            if r.get("stored_path"):
                try:
                    with open(r["stored_path"], "rb") as f:
                        st.download_button(
                            "⬇️ Download CV",
                            data=f,
                            file_name=r.get("original_filename", "cv.pdf"),
                            key=f"dl_{r['screening_id']}",
                        )
                except Exception:
                    st.caption("File not accessible from UI")

            st.markdown("**📧 Contact**")
            email = r.get("email", "")
            st.markdown(f"Email: {email[:2]}***@{email.split('@')[-1]}" if '@' in (email or '') else "Not extracted")
            if is_returning:
                st.markdown("🔁 **Returning Candidate**")
                st.caption("This person has applied before. Check history tab.")

            # Anomalies
            if anomaly_count > 0:
                st.markdown(f"**⚠️ {anomaly_count} Anomaly Flag(s)**")
                st.caption("HR review recommended before deciding.")

            # Decision panel
            st.markdown("---")
            st.markdown("**✍️ HR Decision**")
            current_decision = r.get("decision", "needs_review")
            st.caption(f"Current: {current_decision.replace('_',' ').title()}")

            decision_options = {
                "✅ Shortlist (Approve)": "hr_approved",
                "📞 Schedule Interview":  "forwarded",
                "⏸️ Hold for Later":      "hr_hold",
                "❌ Reject":              "hr_rejected",
            }
            chosen_label = st.selectbox(
                "Update Decision",
                list(decision_options.keys()),
                key=f"dec_{r['screening_id']}",
            )
            decision_notes = st.text_area(
                "Notes (optional)",
                key=f"notes_{r['screening_id']}",
                height=80,
                placeholder="Reason for decision...",
            )
            hr_name = st.text_input("Your Name", key=f"hr_{r['screening_id']}")

            if st.button("Save Decision", key=f"save_{r['screening_id']}", type="primary"):
                if not hr_name:
                    st.error("Please enter your name.")
                else:
                    try:
                        resp = requests.patch(
                            f"{API}/screenings/{r['screening_id']}/decision",
                            data={
                                "decision":    decision_options[chosen_label],
                                "decision_by": hr_name,
                                "notes":       decision_notes,
                            },
                            timeout=10,
                        )
                        if resp.status_code == 200:
                            st.success("✅ Decision saved!")
                            st.rerun()
                        else:
                            st.error(f"Error: {resp.json().get('detail')}")
                    except Exception as e:
                        st.error(str(e))
