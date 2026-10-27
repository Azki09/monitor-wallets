# Monitor de wallets — caso robo USDT en Polygon

Revisa cada ~10 minutos las direcciones del grupo que robó el USDT y manda un correo cuando hay movimiento. Corre gratis en GitHub Actions, sin necesidad de tener una computadora prendida.

## Qué vigila

Por cada dirección de `watchlist.json`:

- **Transferencias de USDT0** que entran o salen (ignora montos menores a 1 USDT, que suelen ser spam de *address poisoning*).
- **Cambios en el saldo de POL.** Si una dirección que tenía 0 POL recibe gas, llega una alerta especial: significa que el grupo se prepara para mover el dinero.
- **Transacciones nuevas enviadas** por la dirección.

Las direcciones de prioridad **alta** (park1, drainer, park2) generan correos con el asunto `[ALERTA ALTA]`. Además llega un **resumen diario** a las ~8:00 (hora del centro de México) para confirmar que el monitor sigue vivo.

Todo lo detectado se guarda en `history.csv` dentro del repo, con fecha, hash y enlaces. Sirve como bitácora para la denuncia.

## Archivos

| Archivo | Para qué |
| --- | --- |
| `monitor.py` | El script. Solo usa Python estándar, sin instalar nada |
| `watchlist.json` | Las direcciones vigiladas y su prioridad |
| `.github/workflows/monitor.yml` | Le dice a GitHub que corra el script cada 10 minutos |
| `state.json` | Memoria del monitor (último bloque revisado y saldos). No lo edites |
| `history.csv` | Bitácora de todos los movimientos detectados |

---

## Paso 1 — Crear el Gmail que envía las alertas

1. Crea una cuenta nueva de Gmail solo para esto (por ejemplo `monitor.wallets.caso@gmail.com`).
2. Entra a <https://myaccount.google.com/security> con esa cuenta y activa la **Verificación en 2 pasos**. Es obligatoria para el siguiente paso.
3. Entra a <https://myaccount.google.com/apppasswords>, escribe un nombre (por ejemplo "monitor") y dale **Crear**.
4. Google te muestra una contraseña de **16 letras**. Cópiala. Solo se muestra una vez. Esta **no** es la contraseña normal de la cuenta.

## Paso 2 — Crear el repositorio en GitHub

1. Entra a <https://github.com/new>.
2. Nombre: por ejemplo `monitor-wallets`.
3. Marca **Public**. Así los minutos de Actions son ilimitados. Las direcciones ya son públicas en la blockchain; las contraseñas van ocultas en *Secrets*.
4. No marques "Add a README". Dale **Create repository**.

## Paso 3 — Subir los archivos

**Opción A, con Git (recomendada).** En la carpeta donde descomprimiste el zip:

```bash
cd monitor-wallets
git init
git add .
git commit -m "Monitor de wallets"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/monitor-wallets.git
git push -u origin main
```

**Opción B, desde la web.** En el repo vacío, da clic en **uploading an existing file** y arrastra `monitor.py`, `watchlist.json`, `state.json`, `history.csv` y `README.md`. Luego **Commit changes**.

La carpeta `.github` suele no subirse arrastrando (es oculta). Créala a mano:

1. **Add file → Create new file**.
2. En el nombre escribe exactamente `.github/workflows/monitor.yml` (al escribir las `/` se crean las carpetas).
3. Pega el contenido del archivo `monitor.yml` del zip y dale **Commit changes**.

## Paso 4 — Guardar las contraseñas como Secrets

En el repo: **Settings → Secrets and variables → Actions → New repository secret**. Crea estos tres:

| Name | Value |
| --- | --- |
| `GMAIL_USER` | El Gmail del Paso 1, por ejemplo `monitor.wallets.caso@gmail.com` |
| `GMAIL_APP_PASSWORD` | La contraseña de 16 letras (con o sin espacios) |
| `ALERT_TO` | Los correos que reciben alertas, separados por coma: `correo.de.tu.amigo@gmail.com,tu.correo@gmail.com` |

Los Secrets no se ven públicamente, ni siquiera en un repo público.

## Paso 5 — Dar permiso de escritura al workflow

**Settings → Actions → General**:

- En **Actions permissions**: *Allow all actions and reusable workflows*.
- En **Workflow permissions**: marca **Read and write permissions** y dale **Save**.

Esto permite que el monitor guarde `state.json` y `history.csv` en el repo.

## Paso 6 — Probar

1. Ve a la pestaña **Actions**. Si aparece un aviso para habilitar workflows, acéptalo.
2. A la izquierda, elige **Monitor de wallets → Run workflow**, selecciona **test** y dale **Run workflow**.
3. En 1–2 minutos debe llegar un correo **"[Prueba] Monitor de wallets funcionando"** con los saldos actuales. Revisa también **Spam** y marca el correo como "No es spam".
4. Corre otra vez con **normal**. La primera corrida normal manda **"[Monitor activado]"** y a partir de ahí corre sola cada 10 minutos.

Si algo falla, entra a la ejecución en rojo dentro de **Actions** y abre el paso **Revisar movimientos** para ver el error.

---

## Qué hacer cuando llegue una alerta

- **`[ALERTA ALTA]` o "LE MANDARON GAS":** el dinero se está moviendo o está por moverse. Toma captura del correo y de los enlaces de Polygonscan y avisa al Ministerio Público con el número de carpeta. Si el destino es un exchange (Polygonscan lo etiqueta, por ejemplo "Binance Hot Wallet"), menciónalo: ese exchange puede congelar y tiene datos del titular.
- **Destino "dirección NO vigilada":** ábrelo en Polygonscan. Si es una wallet nueva del grupo, agrégala a `watchlist.json` (ver abajo) para seguir el rastro.
- **`[Movimiento]` normal:** actividad de las wallets secundarias. Sirve para la bitácora.

## Agregar o quitar direcciones

Edita `watchlist.json` desde GitHub (ícono de lápiz). Cada dirección es un bloque así:

```json
{
  "address": "0xDIRECCION",
  "nombre": "Descripción corta",
  "prioridad": "alta"
}
```

Separa cada bloque con coma y cuida que el último no lleve coma después. `prioridad` puede ser `alta` o `normal`. Para cambiar el monto mínimo de USDT que genera alerta, edita `min_usdt`.

## Ajustes útiles

- **Frecuencia:** en `.github/workflows/monitor.yml`, la línea `cron: "*/10 * * * *"`. Por ejemplo, `*/5` para cada 5 minutos.
- **Pausar:** **Actions → Monitor de wallets → ⋯ → Disable workflow**.

## Limitaciones

- GitHub a veces retrasa las tareas programadas varios minutos en horas de mucha carga. No es tiempo real.
- GitHub desactiva las tareas programadas de repos sin actividad por 60 días. El monitor hace commits al guardar su estado, así que no debería pasar, pero si dejan de llegar los resúmenes diarios, revisa la pestaña **Actions**.
- Usa RPC públicos gratuitos de Polygon. Si uno falla, prueba con otro automáticamente.
- Solo vigila Polygon. Si el dinero cruza a otra blockchain por un bridge, la alerta lo mostrará saliendo, pero hay que seguirlo en el explorador de la otra red.

**Nunca** pongas llaves privadas ni frases semilla en este repo. El monitor no las necesita: solo lee información pública.
