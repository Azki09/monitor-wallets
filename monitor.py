#!/usr/bin/env python3
"""
Monitor de wallets en Polygon (USDT0 + POL) con alertas por correo.

Pensado para correr cada pocos minutos en GitHub Actions. No necesita
librerías externas: solo Python 3.9+ estándar.

Qué vigila de cada dirección en watchlist.json:
  - Transferencias de USDT0 que entran o salen (eventos Transfer del token)
  - Cambios en el saldo de POL (por ejemplo, cuando le mandan gas)
  - Transacciones enviadas por la dirección (cambio de nonce)

Variables de entorno:
  GMAIL_USER          cuenta de Gmail que ENVÍA las alertas
  GMAIL_APP_PASSWORD  contraseña de aplicación de esa cuenta (16 letras)
  ALERT_TO            correos que RECIBEN las alertas, separados por coma
  MODE                "normal" (por defecto) o "test" (manda un correo de prueba)
  RPC_URLS            opcional: lista de RPCs separados por coma
"""

import csv
import json
import os
import smtplib
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

# ----------------------------------------------------------------------------
# Configuración
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
WATCHLIST_FILE = ROOT / "watchlist.json"
STATE_FILE = ROOT / "state.json"
HISTORY_FILE = ROOT / "history.csv"

DEFAULT_RPCS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon-rpc.com",
    "https://polygon.drpc.org",
    "https://1rpc.io/matic",
]
RPC_URLS = [u.strip() for u in os.environ.get("RPC_URLS", "").split(",") if u.strip()] or DEFAULT_RPCS

# USDT0 en Polygon (antes "USDT (PoS)"), 6 decimales
USDT0 = "0xc2132d05d31c914a87c6611c10748aeb04b58e8f"
USDT_DECIMALS = 6
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

CONFIRMACIONES = 5            # bloques de margen para evitar reorganizaciones
TAMANO_TRAMO = 1000           # bloques por consulta eth_getLogs
MAX_BLOQUES_POR_CORRIDA = 20000  # si el monitor estuvo parado, se pone al día por partes
BLOQUES_INICIALES = 300       # primera corrida: revisa ~10 minutos hacia atrás
UMBRAL_POL = 0.01             # ignora cambios de POL menores (polvo / spam)
HORA_RESUMEN_UTC = 14         # resumen diario a partir de las 14:00 UTC (8:00 en CDMX)

MX = timezone(timedelta(hours=-6))  # Centro de México (sin horario de verano)
EXPLORER = "https://polygonscan.com"


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def pad_topic(addr):
    return "0x" + "0" * 24 + addr.lower()[2:]


def topic_to_addr(topic):
    return "0x" + topic[-40:].lower()


def fmt_usdt(x):
    return f"{x:,.2f}"


def fmt_pol(x):
    return f"{x:,.4f}"


def fmt_fecha(ts):
    utc = datetime.fromtimestamp(ts, timezone.utc)
    mx = utc.astimezone(MX)
    return mx.strftime("%d-%m-%Y %H:%M:%S") + " (CDMX)", utc.strftime("%Y-%m-%d %H:%M:%S UTC")


def rpc(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    errores = []
    for url in RPC_URLS:
        for _ in range(2):
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json", "User-Agent": "wallet-monitor/1.0"},
                )
                with urllib.request.urlopen(req, timeout=25) as resp:
                    body = json.loads(resp.read().decode())
                if "error" in body:
                    raise RuntimeError(str(body["error"]))
                return body["result"]
            except Exception as e:  # red caída, límite de peticiones, rango muy grande, etc.
                errores.append(f"{url}: {e}")
                time.sleep(1.5)
    raise RuntimeError("Todos los RPC fallaron:\n  " + "\n  ".join(errores[-6:]))


def get_logs(desde, hasta, topics):
    """eth_getLogs con división automática si el RPC rechaza el rango."""
    try:
        return rpc("eth_getLogs", [{
            "address": USDT0,
            "fromBlock": hex(desde),
            "toBlock": hex(hasta),
            "topics": topics,
        }])
    except RuntimeError:
        if hasta - desde < 20:
            raise
        medio = (desde + hasta) // 2
        return get_logs(desde, medio, topics) + get_logs(medio + 1, hasta, topics)


def block_timestamp(numero, cache):
    if numero not in cache:
        blk = rpc("eth_getBlockByNumber", [hex(numero), False])
        cache[numero] = int(blk["timestamp"], 16)
    return cache[numero]


def snapshot(addr):
    pol = int(rpc("eth_getBalance", [addr, "latest"]), 16) / 1e18
    data = "0x70a08231" + pad_topic(addr)[2:]
    usdt = int(rpc("eth_call", [{"to": USDT0, "data": data}, "latest"]), 16) / 10 ** USDT_DECIMALS
    nonce = int(rpc("eth_getTransactionCount", [addr, "latest"]), 16)
    return {"pol": pol, "usdt": usdt, "nonce": nonce}


def cargar_json(path, defecto):
    if path.exists() and path.read_text().strip():
        return json.loads(path.read_text())
    return defecto


# ----------------------------------------------------------------------------
# Correo
# ----------------------------------------------------------------------------
def enviar_correo(asunto, cuerpo):
    usuario = os.environ.get("GMAIL_USER", "").strip()
    clave = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    destinos = [d.strip() for d in os.environ.get("ALERT_TO", "").split(",") if d.strip()]
    if not (usuario and clave and destinos):
        log("Faltan GMAIL_USER, GMAIL_APP_PASSWORD o ALERT_TO: no se envía correo.")
        log("---- Correo que se habría enviado ----\n" + asunto + "\n\n" + cuerpo)
        return False
    msg = MIMEText(cuerpo, "plain", "utf-8")
    msg["Subject"] = asunto
    msg["From"] = formataddr(("Monitor de wallets", usuario))
    msg["To"] = ", ".join(destinos)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(usuario, clave)
        s.sendmail(usuario, destinos, msg.as_string())
    log(f"Correo enviado a {len(destinos)} destinatario(s): {asunto}")
    return True


def tabla_saldos(watch, snaps):
    lineas = []
    for w in watch:
        a = w["address"].lower()
        s = snaps.get(a)
        if not s:
            continue
        lineas.append(
            f"- {w['nombre']}\n"
            f"  {w['address']}\n"
            f"  USDT0: {fmt_usdt(s['usdt'])} | POL: {fmt_pol(s['pol'])} | tx enviadas: {s['nonce']}\n"
            f"  {EXPLORER}/address/{w['address']}"
        )
    return "\n\n".join(lineas)


def describir_evento(e):
    if e["tipo"] == "transferencia_usdt":
        return (
            f"{'[ALTA] ' if e['prioridad'] == 'alta' else ''}Transferencia de {fmt_usdt(e['monto'])} USDT0\n"
            f"  Fecha: {e['fecha_mx']} / {e['fecha_utc']}\n"
            f"  De:  {e['de_nombre']}\n       {e['de']}\n"
            f"  A:   {e['a_nombre']}\n       {e['a']}\n"
            f"  TX:  {EXPLORER}/tx/{e['tx']}"
        )
    return (
        f"{'[ALTA] ' if e['prioridad'] == 'alta' else ''}{e['detalle']}\n"
        f"  Dirección: {e['de_nombre']}\n       {e['de']}\n"
        f"  Detectado: {e['fecha_mx']} / {e['fecha_utc']}\n"
        f"  Ver: {EXPLORER}/address/{e['de']}"
    )


# ----------------------------------------------------------------------------
# Lógica principal
# ----------------------------------------------------------------------------
def main():
    modo = os.environ.get("MODE", "normal").strip().lower()
    config = cargar_json(WATCHLIST_FILE, {})
    watch = config.get("direcciones", [])
    min_usdt = float(config.get("min_usdt", 1.0))
    if not watch:
        log("watchlist.json no tiene direcciones.")
        return 1

    nombres = {w["address"].lower(): w["nombre"] for w in watch}
    prioridad = {w["address"].lower(): w.get("prioridad", "normal") for w in watch}
    direcciones = list(nombres.keys())

    state = cargar_json(STATE_FILE, {})
    ahora = datetime.now(timezone.utc)
    ahora_mx, ahora_utc = fmt_fecha(ahora.timestamp())

    ultimo = int(rpc("eth_blockNumber", []), 16) - CONFIRMACIONES
    log(f"Bloque actual (con {CONFIRMACIONES} confirmaciones): {ultimo}")

    # ---------------- saldos actuales ----------------
    snaps = {}
    for a in direcciones:
        try:
            snaps[a] = snapshot(a)
        except RuntimeError as e:
            log(f"No se pudo leer el saldo de {a}: {e}")

    # ---------------- modo prueba ----------------
    if modo == "test":
        cuerpo = (
            "Correo de PRUEBA del monitor de wallets. Si te llegó, la configuración funciona.\n\n"
            f"Fecha: {ahora_mx} / {ahora_utc}\n"
            f"Bloque de Polygon: {ultimo}\n\n"
            "Saldos actuales:\n\n" + tabla_saldos(watch, snaps)
        )
        enviar_correo("[Prueba] Monitor de wallets funcionando", cuerpo)
        return 0

    primera_vez = "snapshot" not in state
    eventos = list(state.get("pendientes", []))  # alertas que no se pudieron enviar antes

    # ---------------- transferencias de USDT0 ----------------
    desde = int(state.get("last_block", ultimo - BLOQUES_INICIALES)) + 1
    hasta = min(ultimo, desde + MAX_BLOQUES_POR_CORRIDA - 1)
    if desde <= hasta:
        log(f"Revisando bloques {desde} a {hasta} ({hasta - desde + 1} bloques)")
        padded = [pad_topic(a) for a in direcciones]
        logs = {}
        b = desde
        while b <= hasta:
            fin = min(hasta, b + TAMANO_TRAMO - 1)
            for topics in ([TRANSFER_TOPIC, padded], [TRANSFER_TOPIC, None, padded]):
                for lg in get_logs(b, fin, topics):
                    logs[(lg["transactionHash"], lg["logIndex"])] = lg
            b = fin + 1

        cache_ts = {}
        for lg in sorted(logs.values(), key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16))):
            de = topic_to_addr(lg["topics"][1])
            a = topic_to_addr(lg["topics"][2])
            monto = int(lg["data"], 16) / 10 ** USDT_DECIMALS
            if monto < min_usdt:
                continue  # transferencias de 0 o polvo: típico address poisoning
            bloque = int(lg["blockNumber"], 16)
            fmx, futc = fmt_fecha(block_timestamp(bloque, cache_ts))
            alta = prioridad.get(de) == "alta" or prioridad.get(a) == "alta"
            eventos.append({
                "tipo": "transferencia_usdt",
                "prioridad": "alta" if alta else "normal",
                "fecha_mx": fmx, "fecha_utc": futc, "bloque": bloque,
                "token": "USDT0", "monto": monto,
                "de": de, "de_nombre": nombres.get(de, "dirección NO vigilada"),
                "a": a, "a_nombre": nombres.get(a, "dirección NO vigilada"),
                "tx": lg["transactionHash"], "detalle": "",
            })
        state["last_block"] = hasta
        if hasta < ultimo:
            log(f"Faltan {ultimo - hasta} bloques; se revisan en la siguiente corrida.")

    # ---------------- cambios de POL y de nonce ----------------
    anteriores = state.get("snapshot", {})
    con_transfer = {e["de"] for e in eventos} | {e["a"] for e in eventos}
    for a, s in snaps.items():
        prev = anteriores.get(a)
        if not prev:
            continue
        alta = prioridad.get(a) == "alta"
        base = {
            "fecha_mx": ahora_mx, "fecha_utc": ahora_utc, "bloque": ultimo, "monto": 0,
            "de": a, "de_nombre": nombres[a], "a": "", "a_nombre": "", "tx": "",
        }
        dpol = s["pol"] - prev["pol"]
        if abs(dpol) >= UMBRAL_POL:
            detalle = f"Cambio de saldo POL: {fmt_pol(prev['pol'])} -> {fmt_pol(s['pol'])} ({dpol:+.4f})"
            if prev["pol"] < 0.001 and s["pol"] >= 0.001:
                detalle = ("LE MANDARON GAS (POL) a esta dirección. Ahora puede enviar transacciones: "
                           "es probable que muevan los fondos pronto. " + detalle)
                alta_ev = True
            else:
                alta_ev = alta
            eventos.append({**base, "tipo": "cambio_pol", "token": "POL", "monto": dpol,
                            "prioridad": "alta" if alta_ev else "normal", "detalle": detalle})
        if s["nonce"] > prev["nonce"]:
            n = s["nonce"] - prev["nonce"]
            eventos.append({**base, "tipo": "tx_enviada", "token": "", "prioridad": "alta" if alta else "normal",
                            "detalle": f"La dirección envió {n} transacción(es) nueva(s) (nonce {prev['nonce']} -> {s['nonce']})"})
        dusdt = s["usdt"] - prev["usdt"]
        if abs(dusdt) >= min_usdt and a not in con_transfer:
            eventos.append({**base, "tipo": "cambio_usdt", "token": "USDT0", "monto": dusdt,
                            "prioridad": "alta" if alta else "normal",
                            "detalle": f"Cambio de saldo USDT0: {fmt_usdt(prev['usdt'])} -> {fmt_usdt(s['usdt'])} ({dusdt:+,.2f})"})

    # ---------------- historial ----------------
    nuevos = [e for e in eventos if e not in state.get("pendientes", [])]
    if nuevos:
        nuevo_archivo = not HISTORY_FILE.exists() or HISTORY_FILE.stat().st_size == 0
        with HISTORY_FILE.open("a", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            if nuevo_archivo:
                wr.writerow(["detectado_utc", "tipo", "prioridad", "bloque", "fecha_mx", "token", "monto",
                             "de", "de_nombre", "a", "a_nombre", "tx", "detalle"])
            for e in nuevos:
                wr.writerow([ahora_utc, e["tipo"], e["prioridad"], e["bloque"], e["fecha_mx"], e["token"],
                             e["monto"], e["de"], e["de_nombre"], e["a"], e["a_nombre"], e["tx"], e["detalle"]])

    # ---------------- correos ----------------
    error_correo = False
    if eventos:
        hay_alta = any(e["prioridad"] == "alta" for e in eventos)
        asunto = (f"[{'ALERTA ALTA' if hay_alta else 'Movimiento'}] "
                  f"{len(eventos)} movimiento(s) en las wallets vigiladas")
        cuerpo = (
            ("ATENCIÓN: hubo movimiento en una dirección de prioridad ALTA (donde está el dinero robado).\n"
             "Toma captura de los enlaces y avisa al Ministerio Público con tu número de carpeta.\n\n"
             if hay_alta else "")
            + "\n\n".join(describir_evento(e) for e in eventos)
            + "\n\n------------------------------\nSaldos actuales:\n\n" + tabla_saldos(watch, snaps)
        )
        try:
            enviar_correo(asunto, cuerpo)
            state["pendientes"] = []
        except Exception as e:
            log(f"ERROR al enviar correo: {e}. Se reintentará en la siguiente corrida.")
            state["pendientes"] = eventos
            error_correo = True
    else:
        log("Sin movimientos nuevos.")

    hoy = ahora.astimezone(MX).strftime("%Y-%m-%d")
    if primera_vez or (ahora.hour >= HORA_RESUMEN_UTC and state.get("ultimo_resumen") != hoy):
        titulo = "Monitor activado" if primera_vez else "Resumen diario"
        cuerpo = (
            f"{titulo} — {ahora_mx}\n\n"
            "El monitor revisa las direcciones cada ~10 minutos y te escribe si hay movimiento.\n"
            "Este resumen llega una vez al día para confirmar que sigue funcionando.\n\n"
            "Saldos actuales:\n\n" + tabla_saldos(watch, snaps)
        )
        try:
            enviar_correo(f"[{titulo}] Saldos de las wallets vigiladas", cuerpo)
            state["ultimo_resumen"] = hoy
        except Exception as e:
            log(f"ERROR al enviar resumen: {e}")
            error_correo = True

    if snaps:
        state["snapshot"] = {**anteriores, **snaps}
    state["ultima_revision_utc"] = ahora_utc
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log("Estado guardado.")
    return 1 if error_correo else 0


if __name__ == "__main__":
    sys.exit(main())
