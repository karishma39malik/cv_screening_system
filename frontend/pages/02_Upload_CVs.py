
import streamlit as st
import requests
import time
import os

st.set_page_config(page_title="Upload CVs", page_icon="📤")
st.title("📤 Upload CVs")
st.markdown("Upload candidate CVs for AI-powered screening.")

# --- CHANGE THIS LINE ---
API_BASE = os.getenv("API_URL", "http://api:8000")
API = f"{API_BASE}/api/v1"

# --- Job selection ---
try:
    jobs = requests.get(f"{API}/jobs/", timeout=5).json()
    job_options = {f"{j['title']} — {j.get('department','')}": j['id'] for j in jobs}
except Exception as e:
    job_options = {}
    st.error("Cannot connect to API. Please ensure the system is running.")

if not job_options:
    st.warning("No active jobs found. Please post a job first.")
    st.stop()

selected_job_label = st.selectbox("Select Job Position *", list(job_options.keys()))
selected_job_id    = job_options[selected_job_label]
uploaded_by        = st.text_input("Your Name *", placeholder="HR Recruiter name")

st.markdown("---")

# --- File upload ---
st.subheader("Upload CVs")
st.info("✅ Supported formats: PDF, DOCX, TXT | Max file size: 20MB | Max batch: 500 files")

cv_files = st.file_uploader(
    "Upload one or more CV files",
    type=["pdf", "docx", "txt"],
    accept_multiple_files=True,
    help="You can select multiple files at once. Each CV will be processed independently."
)

if cv_files:
    st.markdown(f"**{len(cv_files)} file(s) selected:**")
    file_data = []
    total_size = 0
    for f in cv_files:
        size_mb = len(f.getvalue()) / (1024*1024)
        total_size += size_mb
        status = "✅" if size_mb <= 20 else "❌ Too large"
        file_data.append({"Filename": f.name, "Size": f"{size_mb:.2f} MB", "Status": status})

    import pandas as pd
    st.dataframe(pd.DataFrame(file_data), use_container_width=True, hide_index=True)
    st.caption(f"Total: {total_size:.1f} MB across {len(cv_files)} files")

if st.button("🚀 Start AI Screening", type="primary", disabled=not (cv_files and uploaded_by)):
    if not uploaded_by:
        st.error("Please enter your name.")
    else:
        with st.spinner(f"Uploading {len(cv_files)} CVs and queuing for AI analysis..."):
            try:
                file_tuples = [
                    ("cv_files", (f.name, f.getvalue(), f.type or "application/octet-stream"))
                    for f in cv_files
                ]
                response = requests.post(
                    f"{API}/screenings/upload",
                    data={"job_id": selected_job_id, "uploaded_by": uploaded_by},
                    files=file_tuples,
                    timeout=120,
                )

                if response.status_code == 200:
                    result = response.json()
                    st.success(f"✅ {result['queued']} CVs queued for AI screening!")

                    col1, col2, col3 = st.columns(3)
                    col1.metric("Total Received", result["total_received"])
                    col2.metric("Queued for Processing", result["queued"])
                    col3.metric("Failed (invalid format/size)", result["failed"])

                    if result["errors"]:
                        st.warning("Some files could not be processed:")
                        for err in result["errors"]:
                            st.markdown(f"- **{err['filename']}**: {err['error']}")

                    st.info("⏳ AI screening is running in the background. "
                            "Navigate to **Screening Results** to view rankings as they appear.")
                else:
                    st.error(f"Upload failed: {response.json().get('detail')}")

            except Exception as e:
                st.error(f"Error: {str(e)}")
