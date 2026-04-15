from fpdf import FPDF

cvs = [
{
"name":"Aarav Khan",
"content":"""Aarav Khan
DATA ENGINEER

Summary:
4+ years experience in building ETL pipelines and data platforms.

Skills:
Python, SQL, Spark, Airflow, AWS, Docker

Experience:
- Built real-time Spark pipelines
- Designed ETL workflows using Airflow
- Managed AWS Redshift warehouse
"""
},
{
"name":"Sara Ali",
"content":"""Sara Ali
DATA ENGINEER

Summary:
Specialist in streaming data pipelines and analytics engineering.

Skills:
Python, SQL, Kafka, Snowflake, Azure

Experience:
- Built Kafka streaming pipelines
- Designed Snowflake data warehouse
- Improved query performance by 30%
"""
},
{
"name":"Omar Hassan",
"content":"""Omar Hassan
SENIOR DATA ENGINEER

Summary:
Expert in distributed systems and large-scale data architecture.

Skills:
Python, Scala, Spark, Databricks, GCP

Experience:
- Built Spark clusters on Databricks
- Designed GCP data lake architecture
- Developed batch + streaming pipelines
"""
}
]

for i, cv in enumerate(cvs, 1):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    for line in cv["content"].split("\n"):
        pdf.cell(200, 8, txt=line, ln=True)

    filename = f"data_engineer_cv_{i}.pdf"
    pdf.output(filename)

    print("Created:", filename)
