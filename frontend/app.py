
import streamlit as st

st.set_page_config(
    page_title="HR Intelligence Platform",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for professional HR look
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: 700;
        color: #1a1a2e;
        margin-bottom: 0.5rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 12px;
        color: white;
        text-align: center;
        margin-bottom: 1rem;
    }
    .score-high   { color: #22c55e; font-weight: bold; }
    .score-mid    { color: #f59e0b; font-weight: bold; }
    .score-low    { color: #ef4444; font-weight: bold; }
    .anomaly-high { background-color: #fee2e2; border-left: 4px solid #ef4444; padding: 8px; margin: 4px 0; border-radius: 4px; }
    .anomaly-med  { background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 8px; margin: 4px 0; border-radius: 4px; }
    .anomaly-low  { background-color: #dbeafe; border-left: 4px solid #3b82f6; padding: 8px; margin: 4px 0; border-radius: 4px; }
    .rationale-box {
        background: #f8f9ff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1rem;
        font-size: 0.95rem;
        line-height: 1.7;
    }
    .returning-badge {
        background: #7c3aed;
        color: white;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
    }
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">🎯 HR Intelligence Platform</div>', unsafe_allow_html=True)
st.markdown("*Agentic AI-powered screening — HR is always in control*")

st.sidebar.title("Navigation")
st.sidebar.info("Use the pages on the left to navigate between sections.")
st.sidebar.markdown("---")
st.sidebar.markdown("**System Status**")

import os
import requests

# 1. Get the URL from the environment variable we set in docker-compose
# This will resolve to http://api:8000 inside Docker
API_URL = os.getenv("API_URL", "http://api:8000")

# 2. Updated Health check block
try:
    # We use the internal Docker URL here
    r = requests.get(f"{API_URL}/health", timeout=5)
    h = r.json()
    st.sidebar.success("✅ API Online")
    st.sidebar.markdown(f"Database: {'✅' if h.get('database') else '❌'}")
    st.sidebar.markdown(f"AI Engine: {'✅' if h.get('ollama') else '❌'}")
except Exception as e:
    st.sidebar.error("❌ API Offline")
    # For debugging, you can uncomment the line below to see the error
    # st.sidebar.write(f"Error: {e}")


st.markdown("### Welcome to the HR Intelligence Platform")
st.markdown("""
Use the sidebar to navigate:
- **📋 Post a Job** — Upload a Job Description
- **📤 Upload CVs** — Submit CVs for screening
- **📊 Screening Results** — View ranked candidates with AI insights
- **📁 Candidate History** — Review past candidates and outcomes
""")
