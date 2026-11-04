FROM python:3.12-alpine

# pdftotext does the PDF text extraction; everything else is stdlib.
RUN apk add --no-cache poppler-utils

WORKDIR /app
COPY waste_pdf_to_yaml.py server.py /app/
COPY static /app/static/

# Container-specific settings. The defaults in server.py suit a developer
# machine; these are what differ inside the add-on:
#  - state goes to the add-on's persistent /data volume
#  - listen on all interfaces so ingress and Home Assistant can reach us
#  - add-on options (set in the add-on's Configuration tab) live in options.json
#  - the UI only accepts the Supervisor ingress proxy (172.30.32.2)
ENV WASTE_DATA_DIR=/data \
    WASTE_HOST=0.0.0.0 \
    WASTE_OPTIONS_FILE=/data/options.json \
    WASTE_UI_ALLOWED_IPS=172.30.32.2

CMD ["python3", "-u", "/app/server.py"]
