# Use official Python image
FROM python:3.12-slim

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose port
EXPOSE 8000

# Set environment variables (optional, for production)
# ENV DATABASE_URL=your_database_url
# ENV TELEGRAM_BOT_TOKEN=your_token
# ENV TELEGRAM_CHANNEL_ID=your_channel_id
# ENV RUN_SCHEDULER=true

# Run the app
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "$PORT"]
# Run the app — use Render dynamic port
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT


