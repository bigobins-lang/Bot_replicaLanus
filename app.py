import cloudscraper
import os
import requests
from datetime import datetime, timezone
import streamlit as st
try:
    from streamlit import st_autorefresh
except ImportError:
    st_autorefresh = None
from google import genai
from dotenv import load_dotenv

load_dotenv()


def get_env_var(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if not value:
        try:
            value = st.secrets[name]
        except Exception:
            value = default
    return value


GEMINI_API_KEY = get_env_var("GEMINI_API_KEY")
TELEGRAM_TOKEN = get_env_var("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID_RAW = get_env_var("TELEGRAM_CHAT_ID")

# Permite múltiples IDs de chat separados por comas. Prioriza el ID de grupo si existe.
def parse_chat_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    ids = []
    for chat_id in raw.split(","):
        normalized = chat_id.strip().strip('"').strip("'")
        if normalized:
            ids.append(normalized)
    return ids

TELEGRAM_CHAT_IDS = parse_chat_ids(TELEGRAM_CHAT_ID_RAW)
TELEGRAM_GROUP_CHAT_IDS = [chat_id for chat_id in TELEGRAM_CHAT_IDS if chat_id.startswith("-100")]
if TELEGRAM_GROUP_CHAT_IDS:
    TELEGRAM_CHAT_IDS = TELEGRAM_GROUP_CHAT_IDS

if not GEMINI_API_KEY:
    raise RuntimeError("Falta GEMINI_API_KEY en el entorno. En Streamlit Cloud agrega el secreto GEMINI_API_KEY en Manage app > Secrets.")
if not TELEGRAM_CHAT_IDS:
    raise RuntimeError("Falta TELEGRAM_CHAT_ID en el entorno o está vacío. En Streamlit Cloud agrega el secreto TELEGRAM_CHAT_ID en Manage app > Secrets.")

GENAI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
# Nota: Asegúrate de usar un modelo válido como gemini-1.5-flash
GENAI_MODEL = "gemini-1.5-flash"

SKILL_PROMPT = """
Eres un analista de datos deportivos experto.
Tu tarea es transformar datos crudos de SofaScore en insights de apuestas.
Enfócate en:
1. Probabilidad de goles basada en tiros a puerta y tiros totales.
2. Flujo de presión con corners, posesión y saque de banda.
3. Eficiencia ofensiva y riesgo defensivo.
Genera el reporte con emojis, formato profesional y recomendaciones claras.
"""


def generate_ai_report(prompt: str) -> str:
    try:
        chat = GENAI_CLIENT.chats.create(model=GENAI_MODEL)
        response = chat.send_message(prompt)
        if hasattr(response, "text") and response.text:
            return response.text.strip()

        return "⚠️ El modelo no pudo generar una respuesta (posible bloqueo de seguridad o respuesta vacía)."
    except Exception as e:
        error_text = str(e)
        if "quota" in error_text.lower() or "429" in error_text:
            st.warning("⚠️ La API de Gemini excedió la cuota o no está disponible. Se usará la recomendación local en su lugar.")
        else:
            st.warning(f"⚠️ No se pudo generar el reporte AI: {error_text}")
        return ""


headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Connection": "keep-alive",
}

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"


def create_sofascore_scraper() -> cloudscraper.CloudScraper:
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "linux", "mobile": False}
    )
    scraper.headers.update(headers)
    scraper.headers.update({
        "Accept-Encoding": "gzip, deflate, br",
        "X-Requested-With": "XMLHttpRequest",
        "Pragma": "no-cache",
        "DNT": "1",
    })
    return scraper


def warm_up_sofascore_session(scraper: cloudscraper.CloudScraper) -> None:
    try:
        scraper.get("https://www.sofascore.com/", timeout=20)
    except Exception:
        pass


def fetch_sofascore_url(url: str) -> dict:
    last_error = None
    for attempt in range(3):
        scraper = create_sofascore_scraper()
        if attempt == 0:
            warm_up_sofascore_session(scraper)

        try:
            response = scraper.get(url, timeout=20)
            if response.status_code == 403 and attempt < 2:
                st.write(f"Intento {attempt + 1}: status={response.status_code}, url={url}")
                continue
            response.raise_for_status()
            return response.json()
        except Exception as e:
            last_error = e
            response_status = None
            try:
                response_status = getattr(e, "response", None).status_code
            except Exception:
                pass

            if response_status == 403 and attempt < 2:
                st.write(f"Intento {attempt + 1}: status={response_status}, url={url}")
                continue

            if attempt == 2:
                st.error(f"Error de red al obtener datos de SofaScore: {e}")
    return {}


def fetch_sofascore_statistics(event_id: str) -> dict:
    url = f"https://api.sofascore.com/api/v1/event/{event_id}/statistics"
    return fetch_sofascore_url(url)


def fetch_sofascore_match_info(event_id: str) -> dict:
    url = f"https://api.sofascore.com/api/v1/event/{event_id}"
    return fetch_sofascore_url(url)


def build_stats_summary(event_id: str, datos: dict) -> str:
    statistics = datos.get("statistics", [])
    summary_lines = [f"Partido SofaScore ID: {event_id}"]

    all_period = next((group for group in statistics if group.get("period") == "ALL"), None)
    items = []
    if all_period:
        for group in all_period.get("groups", []):
            items.extend(group.get("statisticsItems", []))

    interesting_metrics = [
        "Ball possession",
        "Total shots",
        "Shots on target",
        "Corner kicks",
        "Shots inside box",
        "Shots outside box",
        "Goalkeeper saves",
        "Fouls",
        "Offsides",
        "Accurate passes",
        "Final third entries",
    ]

    for item in items:
        name = item.get("name", "")
        if any(keyword.lower() in name.lower() for keyword in interesting_metrics):
            summary_lines.append(f"{name}: {item.get('home', '-')} vs {item.get('away', '-')}")

    if len(summary_lines) == 1:
        summary_lines.append("No se encontraron métricas clave en los datos de SofaScore.")

    return "\n".join(summary_lines)


def parse_numeric_value(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
        if text.endswith('%'):
            return float(text[:-1].strip())
        if '/' in text:
            left, _ = text.split('/', 1)
            return float(left.strip())
        return float(text)
    except ValueError:
        return None


def get_metric_value(items, keywords):
    for item in items:
        name = item.get("name", "")
        if any(keyword.lower() in name.lower() for keyword in keywords):
            return item
    return None


def signal_style(score: int) -> tuple[str, str]:
    if score >= 80:
        return "🟢", "Alta"
    if score >= 60:
        return "🟡", "Media"
    return "🔴", "Baja"


def build_alert_signals(datos: dict) -> list:
    statistics = datos.get("statistics", [])
    all_period = next((group for group in statistics if group.get("period") == "ALL"), None)
    if not all_period:
        return []

    items = []
    for group in all_period.get("groups", []):
        items.extend(group.get("statisticsItems", []))
    signals = []

    shots_item = get_metric_value(items, ["Total shots", "Total shots on goal"])
    sot_item = get_metric_value(items, ["Shots on target", "Shots on goal"])
    corners_item = get_metric_value(items, ["Corner kicks", "Corners"])
    possession_item = get_metric_value(items, ["Ball possession"])
    final_third_item = get_metric_value(items, ["Final third entries"])

    total_shots = sum(parse_numeric_value(x) for x in [shots_item.get("home"), shots_item.get("away")] if x is not None) if shots_item else 0
    sot_total = sum(parse_numeric_value(x) for x in [sot_item.get("home"), sot_item.get("away")] if x is not None) if sot_item else 0
    corner_total = sum(parse_numeric_value(x) for x in [corners_item.get("home"), corners_item.get("away")] if x is not None) if corners_item else 0
    possession_home = parse_numeric_value(possession_item.get("home")) if possession_item else None
    possession_away = parse_numeric_value(possession_item.get("away")) if possession_item else None
    final_third_entries = sum(parse_numeric_value(x) for x in [final_third_item.get("home"), final_third_item.get("away")] if x is not None) if final_third_item else 0

    if total_shots >= 12 and sot_total >= 5:
        signals.append(("Goles", f"Alta probabilidad de goles - {total_shots} tiros totales y {sot_total} tiros a puerta.", 90))
    elif total_shots >= 9 and sot_total >= 4:
        signals.append(("Goles", f"Probabilidad alta de más de 1.5 goles - {total_shots} tiros totales y {sot_total} tiros a puerta.", 75))
    elif total_shots >= 7 and sot_total >= 3:
        signals.append(("Goles", f"Probabilidad moderada de goles; {total_shots} tiros totales y {sot_total} tiros a puerta.", 55))
    else:
        signals.append(("Goles", f"Baja o incierta probabilidad de goles basada en {total_shots} tiros totales y {sot_total} tiros a puerta.", 35))

    if corner_total >= 6:
        signals.append(("Corners", f"Alta probabilidad de más de 4.5 corners - se han generado {corner_total} corners.", 85))
    elif corner_total >= 4:
        signals.append(("Corners", f"Probabilidad moderada de corners elevada; se han generado {corner_total} corners.", 65))
    else:
        signals.append(("Corners", f"Poca probabilidad de un conteo alto de corners por el momento; solo {corner_total} corners.", 30))

    if sot_total >= 5:
        signals.append(("SoT", f"Alta probabilidad de tiros a puerta - {sot_total} tiros a puerta registrados.", 85))
    elif sot_total >= 3:
        signals.append(("SoT", f"Probabilidad moderada de tiros a puerta; {sot_total} tiros a puerta registrados.", 60))
    else:
        signals.append(("SoT", f"Baja probabilidad de tiros a puerta; solo {sot_total} tiros a puerta registrados.", 30))

    if possession_home is not None and possession_away is not None:
        if abs(possession_home - possession_away) >= 15:
            leader = "local" if possession_home > possession_away else "visitante"
            signals.append(("Posesión", f"Fuerte dominio de posesión del equipo {leader}; esto puede indicar un control del ritmo y riesgo defensivo para el rival.", 70))

    if final_third_entries >= 30:
        signals.append(("Final third", "Alta actividad en el último tercio del campo, lo cual refuerza probabilidades de goles y corners.", 80))

    return signals


def get_current_metrics(datos: dict) -> dict:
    metrics = {}
    statistics = datos.get("statistics", [])
    all_period = next((group for group in statistics if group.get("period") == "ALL"), None)
    if not all_period:
        return metrics

    for group in all_period.get("groups", []):
        for item in group.get("statisticsItems", []):
            name = item.get("name", "")
            home = item.get("home")
            away = item.get("away")
            total = None
            home_num = parse_numeric_value(home)
            away_num = parse_numeric_value(away)
            if home_num is not None and away_num is not None:
                total = home_num + away_num
            metrics[name] = {
                "home": home,
                "away": away,
                "total": total,
            }
    return metrics


def get_match_minute(match_info: dict | None) -> str | None:
    if not match_info or "event" not in match_info:
        return None
    event = match_info["event"]
    time_info = event.get("time", {}) or {}
    minute = time_info.get("minute")
    if minute is not None:
        return f"{int(minute)}'"

    cp_ts = time_info.get("currentPeriodStartTimestamp")
    if cp_ts:
        now_ts = datetime.now(timezone.utc).timestamp()
        delta = int((now_ts - cp_ts) / 60) + 1
        if 0 < delta < 120:
            return f"{delta}'"

    status = event.get("status", {})
    description = status.get("description")
    if description and description.lower() not in ["ended", "finished"]:
        return description

    return None


def detect_important_events(current: dict, previous: dict) -> list:
    events = []
    if not previous:
        return events

    def add_delta(name, label, min_delta=1):
        cur = current.get(name)
        prev = previous.get(name)
        if not cur or not prev:
            return
        if cur.get("total") is None or prev.get("total") is None:
            return
        delta = cur["total"] - prev["total"]
        if delta >= min_delta:
            events.append(f"🔔 {label}: +{int(delta)} desde la última actualización.")

    add_delta("Shots on target", "Tiros a puerta", 1)
    add_delta("Corner kicks", "Corners", 1)
    add_delta("Total shots", "Tiros totales", 2)
    add_delta("Final third entries", "Entradas en el último tercio", 5)

    goals_cur = current.get("Goals")
    goals_prev = previous.get("Goals")
    if goals_cur and goals_prev and goals_cur.get("total") is not None and goals_prev.get("total") is not None:
        goal_delta = int(goals_cur["total"] - goals_prev["total"])
        if goal_delta > 0:
            events.append(f"🥅 Gol detectado: +{goal_delta} goles desde la última actualización.")

    possession_cur = current.get("Ball possession")
    possession_prev = previous.get("Ball possession")
    if possession_cur and possession_prev:
        home_cur = parse_numeric_value(possession_cur.get("home"))
        home_prev = parse_numeric_value(possession_prev.get("home"))
        if home_cur is not None and home_prev is not None:
            diff = home_cur - home_prev
            if abs(diff) >= 8:
                team = "local" if diff > 0 else "visitante"
                events.append(f"⚡ Posesión: cambio de {abs(int(diff))}% a favor del {team}.")

    return events


def format_statistics_table(datos: dict) -> None:
    st.subheader("📊 Estadísticas de SofaScore")
    statistics = datos.get("statistics", [])
    if not statistics:
        st.warning("No se encontraron estadísticas en la respuesta de SofaScore.")
        return

    for group in statistics:
        group_name = group.get("groupName", "Grupo")
        with st.expander(group_name):
            rows = []
            for item in group.get("statisticsItems", []):
                rows.append({
                    "Métrica": item.get("name", "-"),
                    "Local": item.get("home", "-"),
                    "Visitante": item.get("away", "-"),
                })
            st.table(rows)


def market_recommendation(title: str, score: int, metrics: dict, match_info: dict | None = None) -> str:
    minute_text = get_match_minute(match_info)
    minute_suffix = f" (min {minute_text})" if minute_text else ""

    if title == "Goles":
        goals_total = metrics.get("Goals", {}).get("total")
        if goals_total is not None and goals_total >= 2:
            return f"Over 1.5 goles ya se cumplió con {int(goals_total)} goles{minute_suffix}; revisa si conviene cerrar o buscar más valor."
        if score >= 80:
            return f"Recomendado: Over 1.5 goles con alta probabilidad{minute_suffix}."
        if score >= 60:
            return f"Posible Over 1.5 goles si el ritmo se mantiene{minute_suffix}."
        return f"No hay señal fuerte de goles todavía{minute_suffix}."
    if title == "Corners":
        corner_total = metrics.get("Corner kicks", {}).get("total")
        if corner_total is not None and corner_total >= 5:
            return f"Más de 4.5 corners ya se cumplió con {int(corner_total)} corners{minute_suffix}; puedes evaluar cerrar o buscar +6.5 si el ritmo sigue."
        if score >= 80:
            return f"Recomendado: Más de 4.5 corners{minute_suffix}."
        if score >= 60:
            return f"Probable conteo de corners elevado; monitorea el partido{minute_suffix}."
        return f"Poca probabilidad de un alta cantidad de corners por ahora{minute_suffix}."
    if title == "SoT":
        sot_total = metrics.get("Shots on target", {}).get("total")
        if sot_total is not None and sot_total >= 5:
            return f"Ya hay {int(sot_total)} tiros a puerta registrados{minute_suffix}; mercado SoT sigue activo si el ritmo ofensivo continúa."
        if score >= 80:
            return f"Recomendado: Tiros a puerta, la opción de SoT tiene buena probabilidad{minute_suffix}."
        if score >= 60:
            return f"Probable aumento en tiros a puerta; sigue el partido{minute_suffix}."
        return f"No hay señal fuerte de tiros a puerta aún{minute_suffix}."
    return ""


def build_recommendation_message(signals: list, metrics: dict, match_info: dict | None = None) -> str:
    if not signals:
        return "No hay señales disponibles para generar una recomendación."

    high = [s for s in signals if s[2] >= 80]
    medium = [s for s in signals if 60 <= s[2] < 80]
    low = [s for s in signals if s[2] < 60]

    if high:
        lines = ["✅ Recomendaciones con alta probabilidad:"]
        for title, message, score in high:
            lines.append(f"• {title} ({score}%): {message}")
            rec = market_recommendation(title, score, metrics)
            if rec:
                lines.append(f"  {rec}")
    elif medium:
        lines = ["⚠️ Señales de atención:"]
        for title, message, score in medium:
            lines.append(f"• {title} ({score}%): {message}")
            rec = market_recommendation(title, score, metrics)
            if rec:
                lines.append(f"  {rec}")
    else:
        lines = ["ℹ️ Actualmente no hay señales de alta probabilidad."]
        for title, message, score in low:
            lines.append(f"• {title} ({score}%): {message}")
        lines.append("\nMantén el seguimiento del partido para tomar decisiones si cambian las estadísticas.")

    return "\n".join(lines)


def build_telegram_summary(match_id: str, datos: dict, signals: list, match_info: dict | None = None, metrics: dict | None = None) -> str:
    title_line = "⚽ REPORTE DE PARTIDO ⚽\n"
    if match_info and match_info.get("event"):
        home = match_info["event"].get("homeTeam", {}).get("name")
        away = match_info["event"].get("awayTeam", {}).get("name")
        if home and away:
            title_line = f"⚽ {home} vs {away} ⚽\n"

    resumen_stats = (
        title_line +
        f"🆔 ID: {match_id}\n"
        "📊 Estadísticas Clave:\n"
    )

    if "statistics" in datos and datos["statistics"]:
        for grupo in datos["statistics"][0].get("groups", []):
            for item in grupo.get("statisticsItems", []):
                if item.get("name") in ["Corner kicks", "Shots on target", "Goals"]:
                    home = item.get("home", "-")
                    away = item.get("away", "-")
                    resumen_stats += f"🔹 {item['name']}: {home} - {away}\n"
    else:
        resumen_stats += "No hay estadísticas disponibles.\n"

    resumen_stats += "\n"
    resumen_stats += build_recommendation_message(signals, metrics or {}, match_info)
    resumen_stats += "\n\n🚀 Generado por ADSO Stats Bot"
    return resumen_stats


def send_telegram_report(text: str) -> list[requests.Response]:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        raise RuntimeError("Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en .env para enviar Telegram. Asegúrate de configurar al menos un ID.")

    responses = []
    for chat_id in TELEGRAM_CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        try:
            response = requests.post(TELEGRAM_URL, json=payload, timeout=20)
            responses.append(response)
        except requests.exceptions.RequestException as e:
            st.error(f"Error de red al enviar a Telegram (chat_id: {chat_id}): {e}")
            # Crear una respuesta dummy para mantener la consistencia del tipo de retorno
            dummy_response = requests.Response()
            dummy_response.status_code = 0  # Indicar un error de red
            dummy_response._content = str(e).encode('utf-8')
            responses.append(dummy_response)
    return responses


# La lógica de la interfaz se ejecuta dentro de main() para permitir python app.py como alias de streamlit run.
def main():
    st.set_page_config(page_title="ADSO Stats Bot", page_icon="⚽")
    st.title("⚽ Analizador de Fútbol Profesional")
    st.sidebar.info("Proyecto ADSO - Cartagena")

    if "match_id" not in st.session_state:
        st.session_state["match_id"] = ""
    if "analyze" not in st.session_state:
        st.session_state["analyze"] = False
    if "last_stats" not in st.session_state:
        st.session_state["last_stats"] = {}
    if "last_update" not in st.session_state:
        st.session_state["last_update"] = ""

    auto_refresh = st.sidebar.checkbox("Actualizar automáticamente", value=False)
    refresh_seconds = st.sidebar.slider("Intervalo de actualización (segundos)", 10, 120, 30, step=5)
    show_events = st.sidebar.checkbox("Mostrar eventos importantes", value=True)

    if auto_refresh:
        if st_autorefresh is not None:
            st_autorefresh(interval=refresh_seconds * 1000, key="auto_refresh")
        else:
            st.warning("Auto-refresh no soportado por esta versión de Streamlit. Actualiza Streamlit o desactiva esta opción.")

    match_id = st.text_input("Ingresa el ID del partido (SofaScore):", value=st.session_state["match_id"], placeholder="Ej: 12318047")
    st.session_state["match_id"] = match_id
    st.caption("Usa un ID válido de SofaScore y espera a que el partido esté en vivo para ver estadísticas en tiempo real.")
    enviar_telegram = st.checkbox("¿Enviar reporte a Telegram?")

    if st.button("📤 Probar conexión Telegram"):
        try:
            test_text = (
                "🔧 Prueba de conexión desde ADSO Stats Bot\n"
                "Si recibes este mensaje, el bot y el chat_id están configurados correctamente."
            )
            telegram_responses = send_telegram_report(test_text)
            all_ok = True
            for i, response in enumerate(telegram_responses):
                chat_id = TELEGRAM_CHAT_IDS[i]
                if response.ok:
                    st.success(f"✅ Prueba enviada a Telegram (chat_id: {chat_id}). Revisa tu chat privado o grupo.")
                else:
                    all_ok = False
                    st.error(f"Error Telegram (chat_id: {chat_id}): {response.status_code} {response.text}")
                    st.info(f"Asegúrate de haber iniciado el bot en Telegram y de usar el chat_id correcto para {chat_id}.")
            if all_ok:
                st.success("✅ Todas las pruebas de conexión a Telegram fueron exitosas.")
            else:
                st.warning("⚠️ Algunas pruebas de conexión a Telegram fallaron. Revisa los errores anteriores.")
        except Exception as e:
            st.error(f"Error al enviar la prueba: {e}")

    if st.button("🚀 Iniciar Análisis Técnico"):
        if not match_id:
            st.warning("Por favor, ingresa un ID válido.")
        else:
            st.session_state["analyze"] = True

    if st.button("⏹️ Detener actualizaciones"):
        st.session_state["analyze"] = False

    if st.session_state["analyze"]:
        if not match_id:
            st.warning("Por favor, ingresa un ID válido.")
        else:
            with st.spinner("Conectando con SofaScore y generando el reporte..."):
                try:
                    datos = fetch_sofascore_statistics(match_id)

                    if not datos or not datos.get("statistics"):
                        st.warning("No se encontraron estadísticas para este partido. Verifica el ID o espera a que el partido esté en vivo.")
                    else:
                        match_info = fetch_sofascore_match_info(match_id)
                        summary_text = build_stats_summary(match_id, datos)
                        signals = build_alert_signals(datos)

                        st.subheader("⚠️ Señales de mercado")
                        for signal in signals:
                            title, message, score = signal
                            emoji, level = signal_style(score)
                            st.markdown(
                                f"**{emoji} {title} — {level}**\n"
                                f"- {message}\n"
                                f"- Probabilidad: **{score}%**"
                            )

                        prompt = (
                            f"{SKILL_PROMPT}\n\n"
                            f"Resumen de estadísticas:\n{summary_text}\n\n"
                            "Genera un conjunto de recomendaciones cortas y claras para goles, corners y tiros a puerta basadas en las señales anteriores."
                        )

                        report_text = generate_ai_report(prompt)
                        if report_text:
                            st.subheader("📋 Reporte de IA")
                            st.markdown(report_text)
                        else:
                            st.subheader("📋 Reporte de IA")
                            st.info("No se generó reporte AI en esta actualización. Se utiliza la recomendación de mercado local.")

                        current_metrics = get_current_metrics(datos)

                        recommendation_text = build_recommendation_message(signals, current_metrics, match_info)
                        st.subheader("✅ Recomendación de mercado")
                        st.markdown(recommendation_text)
                        if show_events:
                            events = detect_important_events(current_metrics, st.session_state.get("last_stats", {}))
                            if events:
                                st.subheader("🚨 Eventos importantes recientes")
                                for event in events:
                                    st.markdown(f"- {event}")
                            else:
                                st.info("No hay eventos importantes desde la última actualización.")

                        st.session_state["last_stats"] = current_metrics
                        st.session_state["last_update"] = datetime.now().strftime("%H:%M:%S")

                        if st.session_state["last_update"]:
                            st.caption(f"Última actualización: {st.session_state['last_update']}")

                        format_statistics_table(datos)

                        if enviar_telegram:
                            telegram_message = build_telegram_summary(match_id, datos, signals, match_info, current_metrics)
                            with st.expander("Vista previa del mensaje de Telegram"):
                                st.code(telegram_message)
                            st.info("Enviando reportes a Telegram...")
                            
                            telegram_responses = send_telegram_report(telegram_message)
                            all_telegram_ok = True
                            for i, response in enumerate(telegram_responses):
                                chat_id = TELEGRAM_CHAT_IDS[i]
                                if response.ok:
                                    st.success(f"📲 Resumen enviado a Telegram (chat_id: {chat_id}) con éxito. Status: {response.status_code}")
                                else:
                                    all_telegram_ok = False
                                    st.error(f"Error Telegram (chat_id: {chat_id}): {response.status_code}")
                                    try:
                                        error_detail = response.json()
                                        st.write(error_detail)
                                    except Exception:
                                        st.write(response.text)
                                    st.info(f"Si el bot no envía mensaje, inicia el bot en Telegram y comprueba el chat_id para {chat_id}.")
                            if all_telegram_ok:
                                st.success("✅ Todos los reportes de Telegram fueron enviados exitosamente.")
                            else:
                                st.warning("⚠️ Algunos reportes de Telegram fallaron. Revisa los mensajes de error arriba.")
                except requests.HTTPError as e:
                    st.error(f"❌ HTTP Error: {e}")
                except Exception as e:
                    st.error(f"💥 Ocurrió un error: {e}")


if __name__ == "__main__":
    import sys
    import streamlit.runtime as rt
    from streamlit.web import cli as stcli

    if not rt.exists():
        sys.argv = ["streamlit", "run", __file__] + sys.argv[1:]
        sys.exit(stcli.main())
    main()
