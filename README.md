# ADSO Stats Bot

Aplicación de Streamlit para análisis de partidos desde SofaScore y generación de reportes con Gemini AI.

Repositorio: https://github.com/bigobins-lang/Bot_replicaLanus

## Contenido
- `app.py`: aplicación principal de Streamlit.
- `requirements.txt`: dependencias necesarias.
- `.gitignore`: archivos y carpetas que no deben subirse al repositorio.

## Requisitos
- Python 3.11+.
- Clonar o copiar el proyecto.
- Crear el entorno virtual y activar.

## Instalación local
```powershell
cd C:\Users\Wizard\Documents\Bot_replicaLanus
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Variables de entorno
Crear un archivo `.env` con las siguientes claves para uso local:

```text
GEMINI_API_KEY=tu_api_key_de_gemini
TELEGRAM_TOKEN=tu_token_de_bot_telegram
TELEGRAM_CHAT_ID=chat_id1,chat_id2
```

En Streamlit Cloud agrega estas claves en el panel de Secrets del app.

## Ejecutar la aplicación
```powershell
python app.py
```

Luego abrir la URL que Streamlit muestre en la terminal (normalmente `http://localhost:8501`).

## Despliegue en la web
### Streamlit Community Cloud
1. Subir el proyecto a un repositorio en GitHub.
2. Crear una app en Streamlit Cloud y conectar con ese repositorio.
3. Configurar el archivo de entrada como `app.py`.
4. Agregar las variables secretas en la interfaz de Streamlit Cloud:
   - `GEMINI_API_KEY`
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Desplegar y compartir la URL pública con tu grupo.

### Otros servicios
También puedes usar plataformas como Render o Fly. El proceso es similar:
- conectar el repositorio de GitHub
- usar `python app.py` como comando de inicio
- configurar variables de entorno

## Notas
- No subas tu archivo `.env` al repositorio.
- Usa `TELEGRAM_CHAT_ID` con IDs separados por comas si quieres enviar a varios chats.
- Si quieres, te ayudo a subirlo a GitHub paso a paso o a preparar un despliegue en Streamlit Cloud.
