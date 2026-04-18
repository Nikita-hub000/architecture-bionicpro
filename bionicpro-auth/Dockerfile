# Используем легкий образ Python
FROM python:3.11-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем список зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем основной код
COPY main.py .

# Запускаем приложение через uvicorn
# --host 0.0.0.0 нужен, чтобы сервис был доступен снаружи контейнера
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]