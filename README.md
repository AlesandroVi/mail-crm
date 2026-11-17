# Mail CRM

App local que lee tu Gmail, arma un CRM de contactos en SQLite y (más adelante)
usa una IA para decidir qué hacer con cada mail.

## Estado

- [x] Paso 1: conectar Gmail y leer mails
- [x] Paso 2: guardar mails y contactos en SQLite
- [x] Paso 3: clasificar con IA (Gemini) en modo simulación
- [x] Paso 4: panel web (mails, contactos y reglas)
- [x] Paso 5: acciones reales desde el panel (papelera, archivar, destacar) con deshacer
- [x] Paso 6: borradores de respuesta (la IA redacta, vos corregís y se guardan en Gmail sin enviarse)
- [ ] Paso 7: aplicación automática

## Estructura

| Archivo | Qué hace |
|---|---|
| `mail_crm/config.py` | Rutas, permisos de Gmail y qué mails traer |
| `mail_crm/db.py` | Tablas `contacts`, `emails`, `decisions` y cómo se guardan |
| `mail_crm/gmail_client.py` | Inicio de sesión con Google y lectura de mails |
| `mail_crm/sync.py` | Trae los mails nuevos y los guarda |

## Conseguir `credentials.json` (una sola vez)

1. Entrá a <https://console.cloud.google.com> y creá un proyecto nuevo (por ejemplo "mail-crm").
2. Menú **APIs y servicios → Biblioteca**, buscá **Gmail API** y tocá **Habilitar**.
3. Menú **APIs y servicios → Pantalla de consentimiento de OAuth**:
   - Tipo de usuario: **Externo**.
   - Nombre de la app y tu mail donde lo pida.
   - En **Usuarios de prueba**, agregá tu propio Gmail.
4. Menú **APIs y servicios → Credenciales → Crear credenciales → ID de cliente de OAuth**:
   - Tipo de aplicación: **App de escritorio**.
   - Descargá el JSON, renombralo a `credentials.json` y ponelo en esta carpeta
     (al lado de este README).

`credentials.json` y `token.json` son secretos: no los compartas ni los subas a GitHub
(ya están en `.gitignore`).

## Correr la sincronización

```bash
.venv\Scripts\python.exe -m mail_crm.sync
```

La primera vez se abre el navegador para que autorices la app. Va a aparecer
"Google no verificó esta app": es normal porque la app es tuya; tocá
**Configuración avanzada → Ir a (nombre de la app)**.

La app pide el permiso **modificar**: puede leer, mover a la papelera, archivar y
destacar mails. No puede borrarlos de forma definitiva. Si la app cambia de
permisos, te pide iniciar sesión de nuevo la próxima vez que corras este comando.

## Clasificar con IA (modo simulación)

1. Sacá una clave gratis en <https://aistudio.google.com/apikey> (no pide tarjeta).
2. Copiá `.env.example` como `.env` y pegá la clave después de `GEMINI_API_KEY=`.
3. Corré:

```bash
.venv\Scripts\python.exe -m mail_crm.clasificar
```

La IA elige una categoría para cada mail y la app anota en la tabla `decisions`
qué acción tomaría. **No toca nada en Gmail.**

| Categoría | Acción que tomaría |
|---|---|
| spam | papelera |
| promocion, notificacion | archivar |
| personal | dejar |
| importante | destacar |
| requiere_respuesta | borrador |

Si la IA tiene menos de 80% de confianza, la acción es `ninguna`. Estas reglas
están en `mail_crm/config.py`.

### Reglas por remitente

Una regla dice "con los mails de este remitente, hacé siempre esto" y manda
sobre la IA. El patrón puede ser un mail exacto o un dominio.

```bash
.venv\Scripts\python.exe -m mail_crm.reglas
.venv\Scripts\python.exe -m mail_crm.reglas agregar tienda.com papelera
.venv\Scripts\python.exe -m mail_crm.reglas borrar tienda.com
```

Acciones posibles: `papelera`, `archivar`, `dejar`, `destacar`.

Para volver a clasificar todo después de cambiar reglas:

```bash
.venv\Scripts\python.exe -m mail_crm.clasificar --rehacer
```

Lo que la IA sabe de vos (por ejemplo, que te interesan las ofertas de empleo)
está en `PERFIL_USUARIO`, dentro de `mail_crm/config.py`.

Si el modelo configurado no existe o no tiene cuota gratuita, mirá cuáles podés usar:

```bash
.venv\Scripts\python.exe -m mail_crm.clasificador
```

y poné el que elijas en `.env` como `GEMINI_MODEL=nombre-del-modelo`.

## Panel web

La forma fácil: doble clic en **`Abrir panel.bat`**. Arranca el servidor en una
ventana minimizada y abre el panel en el navegador. Para apagarlo, cerrá esa ventana.

Para instalarlo como app en la PC: con el panel abierto en Chrome o Edge, tocá el
ícono de instalar en la barra de direcciones (o menú ⋮ → "Instalar Mail CRM").
La app instalada necesita que el servidor esté corriendo, así que abrila después
de `Abrir panel.bat`.

### Contraseña del panel

```bash
.venv\Scripts\python.exe -m mail_crm.clave
```

Te pide una contraseña (mínimo 10 caracteres) y guarda en `.env` solo su hash.
Desde ese momento el panel pide iniciar sesión. Tras 5 intentos fallidos se
bloquea 15 minutos. En tu PC es opcional; en un servidor de internet es
obligatoria (el panel se niega a arrancar sin ella). Para quitarla, borrá las
líneas `PANEL_CLAVE_HASH` y `PANEL_SECRETO` de `.env`.

También se puede arrancar a mano:

```bash
.venv\Scripts\python.exe -m mail_crm.panel
```

Abrí <http://127.0.0.1:8000>. Muestra qué haría la app con cada mail, la lista de
contactos y las reglas, y permite crear o borrar reglas con un clic. El panel solo
es accesible desde tu propia PC. Para cerrarlo, Ctrl+C en la terminal.

El botón **Aplicar en Gmail** ejecuta las decisiones pendientes después de que las
confirmes. Nada se aplica solo. Cada mail aplicado tiene un botón **Deshacer**:

| Acción | Qué hace en Gmail | Deshacer |
|---|---|---|
| Papelera | Mueve a la papelera (se guarda 30 días) | Lo saca de la papelera |
| Archivar | Lo quita de la bandeja de entrada | Lo devuelve a la bandeja |
| Destacar | Le pone la estrella | Se la quita |

## Subir a la nube (para usarlo desde el celular)

Piezas: **Supabase** guarda los datos (Postgres), **Render** corre el panel con
https, **GitHub** guarda el código y dispara la revisión automática cada 3 horas.

1. **Contraseña del panel** (obligatoria en la nube): `python -m mail_crm.clave`
2. **Google en producción:** en Google Cloud → Google Auth Platform → Público →
   **Publicar app**. Después borrá `token.json` y corré `python -m mail_crm.sync`
   para iniciar sesión de nuevo. Así la sesión de Gmail deja de vencer cada 7 días.
3. **Base de datos:** creá un proyecto en Supabase, tocá **Connect** y copiá la
   cadena de conexión **Session pooler**. Pegala en `.env` como `DATABASE_URL=...`
   (con tu contraseña de la base en lugar de `[YOUR-PASSWORD]`).
4. **Copiar tus datos:** `python -m mail_crm.migrar`
5. **Variables del servidor:** `python -m mail_crm.nube` crea `nube.env`.
6. **GitHub:** subí el proyecto a un repositorio **privado**.
7. **Render:** New → Blueprint → elegí el repositorio (usa `render.yaml`). En
   Environment pegá el contenido de `nube.env`. Después borrá `nube.env`.
8. **Revisión automática:** en GitHub → Settings → Secrets and variables → Actions,
   creá `PANEL_URL` (la dirección que te dio Render) y `TAREA_TOKEN` (el de `nube.env`).
9. **Celular:** abrí la dirección de Render en Chrome, iniciá sesión y tocá
   "Agregar a pantalla principal".

Mientras `DATABASE_URL` esté en `.env`, la app de tu PC también usa la base de
la nube (así PC y celular ven lo mismo). Si la quitás, vuelve a usar `crm.db`.

Variables de entorno que entiende la app: `DATABASE_URL`, `GEMINI_API_KEY`,
`GEMINI_MODEL`, `GMAIL_TOKEN_JSON`, `PANEL_HOST`, `PORT`, `PANEL_CLAVE_HASH`,
`PANEL_SECRETO`, `TAREA_TOKEN`.

## Problemas comunes

- **"Se agotó la cuota gratuita de Gemini":** la capa gratuita permite pocos
  pedidos por día. Esperá y volvé a correrlo; los mails que faltan quedan pendientes.

- **Dejó de funcionar después de una semana:** mientras la app esté en modo
  "prueba" en Google Cloud, el inicio de sesión vence a los 7 días. Borrá
  `token.json` y volvé a correr la sincronización.
- **"Access blocked" al iniciar sesión:** falta agregar tu Gmail en "Usuarios de prueba".
