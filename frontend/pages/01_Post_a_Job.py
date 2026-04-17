
import streamlit as st
import requests
import os

st.set_page_config(page_title="Post a Job", page_icon="📋")
st.title("📋 Post a New Job")
st.markdown("Upload a Job Description to begin accepting CV submissions.")

API_BASE = os.getenv("API_URL", "http://api:8000")
API = f"{API_BASE}/api/v1"


with st.form("create_job_form"):
    col1, col2 = st.columns(2)
    with col1:
        title      = st.text_input("Job Title *", placeholder="e.g. Senior Data Engineer")
        department = st.text_input("Department", placeholder="e.g. Technology")
    with col2:
        location   = st.text_input("Location", placeholder="e.g. Dubai, UAE")
        created_by = st.text_input("Your Name *", placeholder="HR Manager name")

    jd_file = st.file_uploader(
        "Upload Job Description *",
        type=["txt", "pdf"],
        help="Plain text or PDF containing the full JD"
    )

    st.markdown("---")
    submitted = st.form_submit_button("🚀 Post Job & Enable CV Screening", type="primary")

if submitted:
    if not all([title, created_by, jd_file]):
        st.error("Please fill in all required fields (*) and upload a JD file.")
    else:
        with st.spinner("Creating job and generating AI embeddings..."):
            try:
                response = requests.post(
                    f"{API}/jobs/",
                    data={"title": title, "department": department,
                          "location": location, "created_by": created_by},
                    files={"jd_file": (jd_file.name, jd_file.getvalue(), jd_file.type)},
                    timeout=60,
                )
                if response.status_code == 201:
                    job = response.json()
                    st.success(f"✅ Job posted successfully!")
                    st.info(f"**Job ID:** `{job['id']}`\n\nShare this ID with your team to upload CVs.")
                    st.balloons()
                else:
                    st.error(f"Error: {response.json().get('detail', 'Unknown error')}")
            except Exception as e:
                st.error(f"Connection error: {str(e)}")

# Show existing jobs
st.markdown("---")
st.subheader("Active Job Postings")
try:
    jobs = requests.get(f"{API}/jobs/", timeout=5).json()
    if jobs:
        for job in jobs:
            with st.expander(f"**{job['title']}** — {job.get('department','')} | {job.get('location','')}"):
                st.code(f"Job ID: {job['id']}", language=None)
                st.markdown(f"Posted: {job['created_at'][:10]}")
    else:
        st.info("No active jobs yet.")
except Exception:
    st.warning("Could not load jobs. Is the API running?")
