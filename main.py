from fastapi import FastAPI, Request, Form, Depends, HTTPException, status, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import bcrypt
import subprocess
from pydantic import BaseModel
import os

from google import genai
from pydantic import BaseModel

# --- Inițializează noul client Gemini (sub importuri) ---
# Atenție: În mod normal, API key-ul se ține în .env, dar pentru hackathon îl lăsăm așa
gemini_client = genai.Client(api_key="AIzaSyCpzy1PFW76ya7BYthlKaHg3cB7qNLMhrY")
# Importăm baza de date
from database import SessionLocal, User, engine, Base

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

# ================= WEBSOCKETS (Colaborare & Chat) =================
# ================= WEBSOCKETS (Colaborare & Chat) =================

# 1. Manager Inteligent pentru Sincronizarea Codului (Fără librării externe!)
class CodeSyncManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.history: list[bytes] = []  # Salvăm fiecare modificare binară!

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # Când cineva intră, îi turnăm în cap tot istoricul de cod binar
        for msg in self.history:
            try:
                await websocket.send_bytes(msg)
            except:
                pass

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: bytes, sender: WebSocket):
        self.history.append(message)  # Adăugăm la istoric

        # Trimitem live celorlalți
        for connection in self.active_connections:
            if connection != sender:
                try:
                    await connection.send_bytes(message)
                except:
                    pass


code_manager = CodeSyncManager()


@app.websocket("/ws/code/{room_name}")
async def websocket_code_endpoint(websocket: WebSocket, room_name: str):
    await code_manager.connect(websocket)
    try:
        while True:
            # Sincronizarea de cod folosește date binare (bytes)
            data = await websocket.receive_bytes()
            await code_manager.broadcast(data, sender=websocket)
    except WebSocketDisconnect:
        code_manager.disconnect(websocket)


# 2. Manager pentru Live Chat (Acum cu Istoric!)
# ... (aici rămâne codul tău de ChatManager exact cum era) ...

# 1. Manager pentru Sincronizarea Codului (Monaco + Yjs)


# 2. Manager pentru Live Chat
# 2. Manager pentru Live Chat (Acum cu Istoric!)
class ChatManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.chat_history: list[str] = []  # Aici salvăm mesajele!

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

        # Trimitem istoricul imediat cum se conectează
        for msg in self.chat_history:
            try:
                await websocket.send_text(msg)
            except:
                pass

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        # Adăugăm în memorie
        self.chat_history.append(message)

        # Păstrăm doar ultimele 50 de mesaje ca să nu încărcăm memoria serverului
        if len(self.chat_history) > 50:
            self.chat_history.pop(0)

        # Trimitem la toată lumea
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass


chat_manager = ChatManager()

@app.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    await chat_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await chat_manager.broadcast(data)
    except WebSocketDisconnect:
        chat_manager.disconnect(websocket)


class TerminalManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str, type: str = "info"):
        import json
        payload = json.dumps({"type": type, "text": message})
        for connection in self.active_connections:
            try:
                await connection.send_text(payload)
            except:
                pass

terminal_manager = TerminalManager()

@app.websocket("/ws/terminal")
async def websocket_terminal_endpoint(websocket: WebSocket):
    await terminal_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # Păstrăm conexiunea deschisă
    except WebSocketDisconnect:
        terminal_manager.disconnect(websocket)
# ================= RUTE GET =================

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.cookies.get("auth_user"):
        return RedirectResponse(url="/editor", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="login.html")

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if request.cookies.get("auth_user"):
        return RedirectResponse(url="/editor", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="register.html")

@app.get("/editor", response_class=HTMLResponse)
async def editor_page(request: Request):
    username = request.cookies.get("auth_user")
    if not username:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="editor.html", context={"username": username})

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("auth_user")
    return response

# ================= RUTE POST =================

@app.post("/register")
async def register_user(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        confirm_password: str = Form(...),
        db: Session = Depends(get_db)
):
    if password != confirm_password:
        return templates.TemplateResponse(request=request, name="register.html", context={"error": "Parolele nu coincid!"})

    db_user = db.query(User).filter(User.username == username).first()
    if db_user:
        return templates.TemplateResponse(request=request, name="register.html", context={"error": "Acest username este deja luat!"})

    new_user = User(username=username, hashed_password=hash_password(password))
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    response = RedirectResponse(url="/editor", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_user", value=username)
    return response

@app.post("/login")
async def login_user(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db)
):
    db_user = db.query(User).filter(User.username == username).first()
    if not db_user or not verify_password(password, db_user.hashed_password):
        return templates.TemplateResponse(request=request, name="login.html", context={"error": "Username sau parolă incorecte!"})

    response = RedirectResponse(url="/editor", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_user", value=username)
    return response

# Structura de date pentru butonul de RUN
class CodePayload(BaseModel):
    code: str


@app.post("/run")
async def run_code(payload: CodePayload):
    # Anunțăm toți utilizatorii că cineva a pornit execuția
    await terminal_manager.broadcast("🔄 Se pregătește mediul izolat (Docker)...", "info")

    # 1. SCANARE VULNERABILITĂȚI (Security Check)
    forbidden = ["os.system", "subprocess", "eval(", "exec(", "open("]
    if any(k in payload.code for k in forbidden):
        msg = "❌ Securitate: Codul conține funcții periculoase blocate de iTECify Sandbox!"
        await terminal_manager.broadcast(msg, "error")
        return {"status": "blocked"}

    try:
        # 2. SANDBOXING CU DOCKER (Smart Resource Limits)
        # Salvăm temporar codul
        filename = f".temp_run_{os.urandom(4).hex()}.py"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(payload.code)

        await terminal_manager.broadcast("▶ Executăm codul...", "info")

        # Rulăm un container Docker efemer (--rm) izolat, fără net, cu limite de CPU/RAM
        # NOTĂ: Trebuie să ai Docker instalat și pornit pe mașina ta!
        cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", "128m",
            "--cpus", "0.5",
            "-v", f"{os.path.abspath(filename)}:/app/main.py:ro",
            "python:3.9-slim",
            "python", "/app/main.py"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        # Curățăm
        if os.path.exists(filename): os.remove(filename)

        # Broadcast la rezultat către toți colaboratorii
        if result.returncode == 0:
            output = result.stdout if result.stdout else "Scriptul a rulat cu succes, dar nu a printat nimic."
            await terminal_manager.broadcast(output, "success")
        else:
            await terminal_manager.broadcast(f"⚠️ Eroare în cod:\n{result.stderr}", "error")

        return {"status": "executed"}

    except subprocess.TimeoutExpired:
        if os.path.exists(filename): os.remove(filename)
        await terminal_manager.broadcast("❌ Timeout: Execuția a fost oprită (limita de 10 secunde).", "error")
        return {"status": "timeout"}
    except Exception as e:
        if os.path.exists(filename): os.remove(filename)
        # Fallback dacă nu aveți Docker instalat pe PC-ul de prezentare
        await terminal_manager.broadcast(f"❌ Eroare de sistem (Ai Docker instalat?): {str(e)}", "error")
        return {"status": "error"}


class AIPayload(BaseModel):
    prompt: str
    context_code: str


# 2. Ruta actualizată cu noul SDK google.genai
@app.post("/api/ai/generate")
async def generate_ai_code(payload: AIPayload):
    try:
        # Instrucțiuni stricte pentru AI
        system_prompt = f"""Ești Ana, un agent AI expert în Python. Ești colega mea de echipă la un hackathon.
        Cerința mea este: {payload.prompt}

        Acesta este codul curent din fișier (ca să știi contextul):
        {payload.context_code}

        REGULI STRICTE:
        1. Răspunde DOAR cu codul sursă generat, fără niciun alt text, salut, sau explicație.
        2. Nu folosi formatare markdown (fără ```python la început sau ``` la final). Doar raw code.
        """

        # Generăm codul asincron folosind noul client (aio = async io)
        response = await gemini_client.aio.models.generate_content(
            model='gemini-1.5-flash',
            contents=system_prompt
        )

        generated_code = response.text.strip()

        # Clean-up de siguranță în caz că AI-ul mai scapă un markdown
        if generated_code.startswith("```python"):
            generated_code = generated_code[9:]
        if generated_code.startswith("```"):
            generated_code = generated_code[3:]
        if generated_code.endswith("```"):
            generated_code = generated_code[:-3]

        return {"code": generated_code.strip()}

    except Exception as e:
        return {"error": f"Ana s-a împiedicat: {str(e)}"}