"""Skill dictionary used for offline resume and job-description matching.

Each entry: canonical name -> list of regex-safe aliases (lowercase). Extend freely.
"""
from __future__ import annotations

import re

_RAW: dict[str, list[str]] = {
    # languages
    "Python": ["python"], "Java": ["java"], "JavaScript": ["javascript", "js", "es6"],
    "TypeScript": ["typescript", "ts"], "C++": ["c\\+\\+", "cpp"], "C": ["c programming", "\\bc\\b(?=[ ,/])"],
    "C#": ["c#", "csharp", "\\.net"], "Go": ["golang", "\\bgo\\b(?= developer| engineer|,|/)"],
    "Rust": ["rust"], "Kotlin": ["kotlin"], "Swift": ["swift"], "Ruby": ["ruby"], "PHP": ["php"],
    "Scala": ["scala"], "R": ["\\br\\b(?= programming|,|/)"], "Dart": ["dart"], "SQL": ["sql"],
    "Bash": ["bash", "shell scripting"], "MATLAB": ["matlab"], "Solidity": ["solidity"],
    # web / frontend
    "React": ["react", "react\\.js", "reactjs"], "React Native": ["react native"], "Next.js": ["next\\.js", "nextjs"],
    "Angular": ["angular"], "Vue": ["vue", "vue\\.js", "nuxt"], "Svelte": ["svelte"], "Redux": ["redux"],
    "HTML": ["html", "html5"], "CSS": ["css", "css3", "scss", "sass"], "Tailwind": ["tailwind"],
    "Figma": ["figma"], "Expo": ["expo"], "Flutter": ["flutter"], "jQuery": ["jquery"], "Webpack": ["webpack", "vite"],
    # backend
    "Node.js": ["node\\.js", "nodejs", "node"], "Express": ["express", "express\\.js"], "FastAPI": ["fastapi"],
    "Flask": ["flask"], "Django": ["django"], "Spring Boot": ["spring boot", "spring"], "NestJS": ["nestjs"],
    "GraphQL": ["graphql"], "REST APIs": ["rest api", "rest apis", "restful"], "gRPC": ["grpc"],
    "Microservices": ["microservices", "micro-services"], "Laravel": ["laravel"], "Rails": ["rails"],
    "WebSockets": ["websocket", "websockets"],
    # data stores
    "PostgreSQL": ["postgres", "postgresql"], "MySQL": ["mysql"], "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"], "Elasticsearch": ["elasticsearch", "opensearch"], "Cassandra": ["cassandra"],
    "DynamoDB": ["dynamodb"], "SQLite": ["sqlite"], "Supabase": ["supabase"], "Firebase": ["firebase", "firestore"],
    "Snowflake": ["snowflake"], "BigQuery": ["bigquery"], "Oracle DB": ["oracle database", "pl/sql"],
    # cloud / devops
    "AWS": ["aws", "amazon web services", "ec2", "s3", "lambda"], "GCP": ["gcp", "google cloud"],
    "Azure": ["azure"], "Docker": ["docker"], "Kubernetes": ["kubernetes", "k8s"], "Terraform": ["terraform"],
    "Ansible": ["ansible"], "Jenkins": ["jenkins"], "CI/CD": ["ci/cd", "ci cd", "continuous integration"],
    "GitHub Actions": ["github actions"], "GitLab CI": ["gitlab ci", "gitlab"], "Git": ["git"],
    "Linux": ["linux", "unix"], "Nginx": ["nginx"], "OpenShift": ["openshift"], "Helm": ["helm"],
    "Prometheus": ["prometheus"], "Grafana": ["grafana"], "Datadog": ["datadog"], "Serverless": ["serverless"],
    "Kafka": ["kafka"], "RabbitMQ": ["rabbitmq"], "Airflow": ["airflow"], "Spark": ["spark", "pyspark"],
    "Hadoop": ["hadoop"], "dbt": ["\\bdbt\\b"], "ETL": ["etl", "elt", "data pipelines?"],
    # AI / ML
    "Machine Learning": ["machine learning", "\\bml\\b"], "Deep Learning": ["deep learning"],
    "NLP": ["nlp", "natural language processing"], "Computer Vision": ["computer vision", "opencv"],
    "PyTorch": ["pytorch", "torch"], "TensorFlow": ["tensorflow", "keras"], "scikit-learn": ["scikit-learn", "sklearn"],
    "Pandas": ["pandas"], "NumPy": ["numpy"], "LLMs": ["llm", "llms", "large language models?", "gpt", "genai", "generative ai"],
    "LangChain": ["langchain", "langgraph"], "RAG": ["\\brag\\b", "retrieval augmented"], "Hugging Face": ["hugging ?face", "transformers"],
    "MLOps": ["mlops"], "Prompt Engineering": ["prompt engineering"], "AI Agents": ["ai agents?", "agentic", "mcp"],
    "CNN": ["\\bcnn\\b", "resnet"], "Time Series": ["time series", "forecasting", "n-beats"],
    "Statistics": ["statistics", "statistical"], "Data Analysis": ["data analysis", "data analytics"],
    "Power BI": ["power bi", "powerbi"], "Tableau": ["tableau"], "Excel": ["excel", "spreadsheets"],
    "Vector DBs": ["pinecone", "weaviate", "faiss", "chroma", "vector database"],
    # testing / quality
    "pytest": ["pytest"], "Jest": ["jest"], "Selenium": ["selenium"], "Playwright": ["playwright"],
    "Cypress": ["cypress"], "Unit Testing": ["unit test", "unit testing", "tdd"], "QA": ["\\bqa\\b", "quality assurance"],
    # mobile
    "Android": ["android"], "iOS": ["\\bios\\b"],
    # practices / misc
    "System Design": ["system design", "distributed systems"], "Data Structures": ["data structures", "algorithms", "dsa"],
    "OOP": ["oop", "object oriented"], "Agile": ["agile", "scrum"], "Jira": ["jira"],
    "Security": ["security", "owasp", "authentication", "oauth"], "Networking": ["networking", "tcp/ip"],
    "Blockchain": ["blockchain", "web3"], "Embedded": ["embedded", "rtos", "microcontroller"],
    "SAP": ["\\bsap\\b"], "Salesforce": ["salesforce"], "ServiceNow": ["servicenow"],
    "Product Management": ["product management", "roadmap"], "UI/UX": ["ui/ux", "ux design", "user experience"],
    "Communication": ["communication skills", "stakeholder"], "AIOps": ["aiops"],
    "Appwrite": ["appwrite"], "Supabase Auth": ["supabase auth"],
}

SKILLS: dict[str, re.Pattern] = {
    name: re.compile(r"(?<![a-z0-9+#])(?:" + "|".join(aliases) + r")(?![a-z0-9+#])", re.I)
    for name, aliases in _RAW.items()
}


_NAMES = list(_RAW)
_COMBINED = re.compile(
    r"(?<![a-z0-9+#])(?:" + "|".join(f"(?P<g{i}>" + "|".join(_RAW[n]) + ")" for i, n in enumerate(_NAMES)) + r")(?![a-z0-9+#])",
    re.I,
)


def find_skills(text: str) -> list[str]:
    """One pass over the text with a combined pattern (fast enough for thousands of job descriptions)."""
    if not text:
        return []
    found = {m.lastgroup for m in _COMBINED.finditer(text)}
    return [n for i, n in enumerate(_NAMES) if f"g{i}" in found]
