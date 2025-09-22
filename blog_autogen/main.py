#!/usr/bin/env python3
import os
import csv
import sys
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, date
from zoneinfo import ZoneInfo
import unicodedata
import re

from openai import OpenAI  # SDK oficial de OpenAI

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
TOPICS_CSV = os.path.join(REPO_ROOT, "topics.csv")
OUT_DIR = os.path.join(REPO_ROOT, "out")

# ---------- Utilidades ----------
def slugify(text):
    text = text.lower().strip()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'[^a-z0-9\-_\s]', '', text)
    text = re.sub(r'\s+', '-', text)
    text = re.sub(r'-{2,}', '-', text)
    return text.strip('-')

def ensure_outdir():
    os.makedirs(OUT_DIR, exist_ok=True)

def write_markdown(md_text, today_str):
    title_match = re.search(r'^\s*#\s+(.+)', md_text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "Entrada de blog"
    slug = slugify(title)
    fname = f"{today_str}--{slug}.md"
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

# ---------- Lectura de calendario ----------
def read_schedule(path):
    """
    Soporta dos formatos:
    1) 'YYYY-MM-DD,Título...'  -> calendario fijo por fecha
    2) 'Título...'             -> lista simple (fallback)
    Devuelve:
      - schedule: lista de (date, title) si hay fechas
      - topics:   lista de títulos si no hay fechas
    """
    schedule = []
    topics = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            line = row[0].strip()
            if not line or line.startswith('#'):
                continue
            if len(row) >= 2:  # formato con fecha + título separado por coma
                d_str = row[0].strip()
                title = ",".join(row[1:]).strip()
                try:
                    d = date.fromisoformat(d_str)
                    schedule.append((d, title))
                except ValueError:
                    # si no parsea como fecha ISO, lo tratamos como título suelto
                    topics.append(line)
            else:
                topics.append(line)

    if schedule:
        # ordenar por fecha, por si acaso
        schedule.sort(key=lambda x: x[0])
        return schedule, None
    if topics:
        return None, topics
    raise RuntimeError("El archivo topics.csv está vacío o mal formateado.")

def pick_topic_by_date(schedule, today):
    """
    Si hoy coincide con una fecha del calendario -> usa ese tema.
    Si no coincide:
      - si hoy es antes del primer tema futuro -> usa el primer futuro (>= hoy)
      - si hoy es después de todos -> usa el último (o None si prefieres no enviar)
    """
    # exact match
    for d, title in schedule:
        if d == today:
            return title, d

    # siguiente fecha >= hoy
    for d, title in schedule:
        if d >= today:
            return title, d

    # si hemos pasado todas las fechas, coge la última (o cambia a None si no quieres enviar)
    last_d, last_title = schedule[-1]
    return last_title, last_d

def pick_topic_rotating(topics, today):
    # rotación por semana ISO si no hay calendario
    iso_week = today.isocalendar().week
    idx = (iso_week - 1) % len(topics)
    return topics[idx]

def build_prompt(topic):
    return f"""Eres un arquitecto senior en España y escribes para un blog propio.
Tu tono debe sonar a Enrique: 
- Profesional y claro, sin rodeos.
- Cercano y expresivo, con chispa y naturalidad.
- Contundente: habla como alguien que sabe y no vende humo.
- Con visión de futuro y referencias prácticas a 2025.
- Humor breve e inteligente cuando encaje (nunca forzado).
- Siempre útil para comunidades de propietarios y clientes que quieren rehabilitar o mejorar su edificio.

Escribe un post en español (900–1200 palabras) en formato Markdown que incluya:
- Un título atractivo y preciso.
- Un resumen ejecutivo de 3–5 líneas.
- 4–6 secciones con subtítulos y bullets accionables.
- Rangos de precios orientativos 2025 (aclarar que son estimaciones).
- Riesgos habituales y cómo evitarlos.
- Un checklist final.
- Una llamada a la acción (CTA) clara.

Incluye al inicio un bloque de metadatos en YAML con:
title, date, slug, tags, excerpt, seo_title, seo_description.

Tema de esta semana: {topic}
"""

def main():
    # ----- Zona horaria -----
    tz_name = os.getenv("TIMEZONE", "Europe/Madrid")
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    today_str = today.isoformat()

    # ----- Config -----
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

    # ----- Selección de tema -----
    schedule, topics = read_schedule(TOPICS_CSV)

    if topic_override:
        chosen_topic = topic_override
        chosen_date = today
    elif schedule:
        chosen_topic, chosen_date = pick_topic_by_date(schedule, today)
    else:
        chosen_topic = pick_topic_rotating(topics, today)
        chosen_date = today

    # ----- Llamada al modelo -----
    client = OpenAI(api_key=openai_api_key)
    prompt = build_prompt(chosen_topic)
    resp = client.responses.create(model=model, input=prompt)
    md_text = resp.output_text

    # ----- Guardar y enviar -----
    ensure_outdir()
    md_path, title = write_markdown(md_text, today_str)

    subject = f"Borrador Blog – {today_str} – {title}"
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

    print(f"OK | Fecha: {today_str} | Tema: {chosen_topic} | Archivo: {md_path} | Email: {smtp_to}")

if __name__ == "__main__":
    main()
