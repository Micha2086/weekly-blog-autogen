#!/usr/bin/env python3
import os
import csv
import sys
import smtplib
import ssl
from email.message import EmailMessage
from datetime import date
import unicodedata
import re

from openai import OpenAI  # SDK oficial de OpenAI

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
TOPICS_CSV = os.path.join(REPO_ROOT, "topics.csv")
OUT_DIR = os.path.join(REPO_ROOT, "out")

def slugify(text):
    text = text.lower().strip()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'[^a-z0-9\-_\s]', '', text)
    text = re.sub(r'\s+', '-', text)
    text = re.sub(r'-{2,}', '-', text)
    return text.strip('-')

def read_topics(path):
    topics = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            title = row[0].strip()
            if title and not title.startswith('#'):
                topics.append(title)
    if not topics:
        raise RuntimeError("El archivo topics.csv está vacío.")
    return topics

def pick_topic(topics, override=None):
    if override:
        return override
    iso_week = date.today().isocalendar().week
    idx = (iso_week - 1) % len(topics)
    return topics[idx]

def build_prompt(topic):
    return f"""Eres un arquitecto senior en España. Redacta un post en español (900–1200 palabras) en formato Markdown para un blog profesional.
Estilo: profesional, directo y cercano; contundente y sin humo; con humor ligero cuando tenga sentido. 
Público: comunidades de propietarios y clientes que evalúan rehabilitación/obras en edificios residenciales.
Año: 2025 (cita precios como estimaciones, no promesas).
Incluye:
- Título atractivo pero claro
- Resumen ejecutivo (3–5 líneas)
- 4–6 secciones con subtítulos y bullets accionables
- Rangos de precios orientativos 2025 cuando aplique (aclarar que son estimaciones)
- Riesgos habituales y cómo evitarlos
- Checklist ejecutable
- CTA final
- Metadatos en cabecera YAML: title, date, slug, tags, excerpt, seo_title, seo_description

Tema de esta semana: {topic}
"""

def ensure_outdir():
    os.makedirs(OUT_DIR, exist_ok=True)

def write_markdown(md_text):
    title_match = re.search(r'^\s*#\s+(.+)', md_text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "Entrada de blog"
    slug = slugify(title)
    today = date.today().isoformat()
    fname = f"{today}--{slug}.md"
    fpath = os.path.join(OUT_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(md_text)
    return fpath, title

def send_email(smtp_host, smtp_port, username, password, sender, recipients, subject, body, attachment_path=None):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipients
    msg.set_content("Versión en texto plano del borrador adjunta.\n\n" + body[:1000])
    msg.add_alternative(f"""\
<html>
  <body>
    <p>Hola,<br><br>
       Aquí tienes el borrador semanal para tu blog.<br>
       Puedes copiar/pegar el contenido Markdown o descargar el archivo adjunto.<br><br>
       <strong>Vista previa (primeras líneas):</strong>
    </p>
    <pre style="white-space:pre-wrap; font-family:monospace; border:1px solid #ddd; padding:12px;">{body[:5000]}</pre>
    <p>— Generado automáticamente.</p>
  </body>
</html>
""", subtype="html")

    if attachment_path:
        with open(attachment_path, "rb") as f:
            data = f.read()
        msg.add_attachment(data, maintype="text", subtype="markdown", filename=os.path.basename(attachment_path))

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
        server.starttls(context=context)
        server.login(username, password)
        server.send_message(msg)

def main():
    openai_api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("MODEL", "gpt-4.1-mini")
    topic_override = os.getenv("TOPIC_OVERRIDE", "").strip() or None

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = os.getenv("SMTP_PORT", "587")
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM")
    smtp_to = os.getenv("SMTP_TO")

    if not openai_api_key:
        print("Falta OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)
    if not all([smtp_username, smtp_password, smtp_from, smtp_to]):
        print("Faltan variables SMTP (USERNAME/PASSWORD/FROM/TO)", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=openai_api_key)

    topics = read_topics(TOPICS_CSV)
    topic = pick_topic(topics, topic_override)
    prompt = build_prompt(topic)

    resp = client.responses.create(model=model, input=prompt)
    md_text = resp.output_text

    ensure_outdir()
    md_path, title = write_markdown(md_text)

    subject = f"Borrador Blog – {date.today().isoformat()} – {title}"
    send_email(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        username=smtp_username,
        password=smtp_password,
        sender=smtp_from,
        recipients=smtp_to,
        subject=subject,
        body=md_text,
        attachment_path=md_path
    )

    print(f"OK | Topic: {topic} | File: {md_path} | Email sent to: {smtp_to}")

if __name__ == "__main__":
    main()
