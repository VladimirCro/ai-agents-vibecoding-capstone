FROM python:3.12-slim

# Install dependencies
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create a non-root user for security
RUN addgroup --system worknote && adduser --system --group worknote
USER worknote

# Cloud Run serves on this port
ENV PORT=8080
EXPOSE 8080

# exec-form CMD — PID 1 safe (SIGTERM forwarded directly)
CMD ["/usr/local/bin/entrypoint.sh"]
