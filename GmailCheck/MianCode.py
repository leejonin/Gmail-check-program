import base64
import datetime as dt
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

# 아래 라이브러리는 pip로 설치가 필요합니다: pyttsx3
# pip install pyttsx3

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# UI/결과에서 반드시 보여줄 분야(고정)
REQUIRED_CATEGORIES: list[str] = [
    "TLDR",
    "AI",
    "InfoSec",
    "IT",
    "Design",
    "Crypto",
    "Dev",
    "Marketing",
    "Founders",
    "DevOps",
    "Product",
    "Fintech",
    "Data",
]

_CATEGORY_CANONICAL: dict[str, str] = {
    "tldr": "TLDR",
    "ai": "AI",
    "infosec": "InfoSec",
    "it": "IT",
    "design": "Design",
    "crypto": "Crypto",
    "dev": "Dev",
    "marketing": "Marketing",
    "founders": "Founders",
    "devops": "DevOps",
    "product": "Product",
    "fintech": "Fintech",
    "data": "Data",
}


def _canonicalize_category(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return "TLDR"
    return _CATEGORY_CANONICAL.get(s.lower(), s)


@dataclass
class GmailMessage:
    message_id: str
    subject: str
    from_header: str
    internal_date: int  # ms epoch
    snippet: str
    body_text: str

    @property
    def received_datetime_local(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.internal_date / 1000.0)


def _load_gmail_service():
    creds = None
    token_path = "token.json"
    cred_path = "credentials.json"

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except (json.JSONDecodeError, ValueError):
            # 빈/깨진 token.json이면 삭제 후 OAuth 재진행
            try:
                os.remove(token_path)
            except OSError:
                pass
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(cred_path):
                raise FileNotFoundError(
                    "credentials.json 파일이 없습니다. Google Cloud에서 OAuth 데스크톱 클라이언트를 만들고 "
                    "발급된 JSON을 이 폴더에 credentials.json 로 저장하세요."
                )
            try:
                if os.path.getsize(cred_path) == 0:
                    raise ValueError("credentials.json이 비어있습니다.")
            except OSError:
                pass
            try:
                flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
            except (json.JSONDecodeError, ValueError) as e:
                raise ValueError(
                    "credentials.json이 올바른 JSON 형식이 아니거나 내용이 비어있습니다.\n\n"
                    "해결 방법:\n"
                    "- Google Cloud Console → APIs & Services → Credentials\n"
                    "- OAuth 2.0 Client IDs에서 'Desktop app' 클라이언트를 선택\n"
                    "- 'Download JSON'으로 받은 파일을 이 폴더에 credentials.json 로 저장\n\n"
                    f"원인: {e}"
                )
            creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _today_query_local() -> str:
    # Gmail 검색 단계에서 from:TLDR이 "표시 이름(TLDR ...)"을 항상 잡지 못하는 경우가 있어
    # 검색은 넓게(최근 N일 + TLDR 키워드) 가져오고,
    # 실제 TLDR 판정은 From 헤더(표시 이름) 기반으로 코드에서 필터합니다.
    # in:anywhere: 보관처리(Archive)된 메일 포함
    # (is:read OR is:unread): 읽은 메일 / 안 읽은 메일 모두 포함
    return "newer_than:2d in:anywhere (is:read OR is:unread) TLDR"


def _extract_sender_display_name(from_header: str) -> str:
    """
    예시:
    - TLDR Dev <noreply@x.com>  -> TLDR Dev
    - "TLDR Dev" <noreply@x.com> -> TLDR Dev
    - noreply@x.com -> noreply@x.com (표시 이름이 없으면 그대로)
    """
    s = (from_header or "").strip()
    if not s:
        return ""
    # angle addr 있으면 앞부분이 display name
    if "<" in s and ">" in s:
        name = s.split("<", 1)[0].strip()
    else:
        name = s
    # 따옴표 제거
    name = name.strip().strip('"').strip("'").strip()
    return name


def _extract_email_address(from_header: str) -> str:
    """From 헤더에서 이메일 주소만 추출. 예: TLDR Dev <dan@tldrnewsletter.com> -> dan@tldrnewsletter.com"""
    s = (from_header or "").strip()
    m = re.search(r"<([^>]+)>", s)
    if m:
        return m.group(1).strip().lower()
    # angle bracket 없으면 전체가 이메일
    return s.lower()


def _is_tldr_sender(from_header: str) -> bool:
    email = _extract_email_address(from_header)
    # 도메인이 tldrnewsletter.com이면 무조건 허용
    if "tldrnewsletter.com" in email:
        return True
    name = _extract_sender_display_name(from_header).upper()
    if "TLDR" in name: # 시작이 아니더라도 포함만 되어도 허용
        return True
    return False


def _get_header(headers, name: str) -> str:
    name_l = name.lower()
    for h in headers or []:
        if (h.get("name") or "").lower() == name_l:
            return h.get("value") or ""
    return ""


def _decode_b64url(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _extract_text_from_payload(payload) -> str:
    if not payload:
        return ""

    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(node):
        if not node:
            return
        mt = (node.get("mimeType") or "").lower()
        body = node.get("body", {}) or {}
        data = body.get("data")
        if data:
            text = _decode_b64url(data)
            if mt == "text/plain":
                if text.strip():
                    plain_parts.append(text)
            elif mt == "text/html":
                if text.strip():
                    html_parts.append(text)
            else:
                # multipart/* 혹은 기타 타입이라면, 하위 parts가 있으면 거기서 수집
                pass

        for p in node.get("parts") or []:
            walk(p)

    walk(payload)

    if plain_parts:
        return "\n\n".join(plain_parts).strip()
    if html_parts:
        return _strip_html("\n\n".join(html_parts)).strip()
    return ""


def _strip_html(html: str) -> str:
    # 매우 단순한 HTML 제거(메일 본문 보조용)
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</p\s*>", "\n\n", html)
    html = re.sub(r"(?is)<.*?>", "", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n{3,}", "\n\n", html).strip()

def fetch_today_tldr_messages() -> list[GmailMessage]:
    service = _load_gmail_service()
    user_id = "me"
    q = _today_query_local()
    today_local = dt.date.today()
    yesterday_local = today_local - dt.timedelta(days=1)
    
    # 추가: 그저께 날짜 변수 생성 (시차 문제 해결용)
    two_days_ago_local = today_local - dt.timedelta(days=2)

    messages: list[GmailMessage] = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(
                userId=user_id,
                q=q,
                pageToken=page_token,
                includeSpamTrash=False,
                maxResults=500,
            )
            .execute()
        )
        for m in resp.get("messages", []):
            msg = (
                service.users()
                .messages()
                .get(userId=user_id, id=m["id"], format="full")
                .execute()
            )
            payload = msg.get("payload") or {}
            headers = payload.get("headers") or []
            subject = _get_header(headers, "Subject")
            from_header = _get_header(headers, "From")
            internal_date = int(msg.get("internalDate") or 0)
            snippet = msg.get("snippet") or ""
            body_text = _extract_text_from_payload(payload) or ""

            gm = GmailMessage(
                message_id=m["id"],
                subject=subject,
                from_header=from_header,
                internal_date=internal_date,
                snippet=snippet,
                body_text=body_text,
            )

            # 넓게 검색한 뒤, 여기서 엄밀히 필터
            # 최근 3일(TLDR Fintech / TLDR Data 등 시차로 어긋나는 메일까지 포함)로 조건 완화
            d = gm.received_datetime_local.date()
            if _is_tldr_sender(gm.from_header) and d in (today_local, yesterday_local, two_days_ago_local):
                messages.append(gm)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return messages

def parse_category_from_sender(from_header: str, subject: str = "") -> str:
    """
    발신자 표시 이름 또는 제목(Subject)에서 TLDR 카테고리를 추출합니다.
    - 표시 이름 우선: "TLDR Fintech <dan@tldrnewsletter.com>" -> "Fintech"
    - 표시 이름이 없거나 단순 이메일인 경우 Subject에서 파싱:
        "TLDR Fintech Newsletter" -> "Fintech"
        "TLDR Data - ..." -> "Data"
    """
    # 1) 표시 이름에서 시도
    s = _extract_sender_display_name(from_header)
    m = re.search(r"(?i)TLDR\s+([A-Za-z0-9]+)", s)
    if m:
        raw = m.group(1).strip()
        canonical = _canonicalize_category(raw)
        print(f"[DEBUG] (발신자명) From: {from_header!r} -> raw: {raw!r} -> canonical: {canonical!r}")
        return canonical

    # 2) 표시 이름 실패 시 Subject에서 시도
    #    예: "TLDR Fintech Newsletter" / "TLDR Data | ..." / "TLDR AI #123"
    subj = (subject or "").strip()
    m2 = re.search(r"(?i)TLDR\s+([A-Za-z0-9]+)", subj)
    if m2:
        raw = m2.group(1).strip()
        canonical = _canonicalize_category(raw)
        print(f"[DEBUG] (제목) Subject: {subj!r} -> raw: {raw!r} -> canonical: {canonical!r}")
        return canonical

    print(f"[DEBUG] 카테고리 파싱 실패 - From: {from_header!r} / Subject: {subj!r}")
    return "TLDR"


def group_by_category(messages: list[GmailMessage]) -> dict[str, list[GmailMessage]]:
    grouped: dict[str, list[GmailMessage]] = {}
    for msg in messages:
        cat = parse_category_from_sender(msg.from_header, msg.subject)
        grouped.setdefault(_canonicalize_category(cat), []).append(msg)
    # 각 분야별로 날짜/시간 기준 최신 메일 1개만 유지
    for cat in list(grouped.keys()):
        if grouped[cat]:
            grouped[cat] = [max(grouped[cat], key=lambda m: m.internal_date)]
    # 고정 분야가 항상 존재하도록 보정(0개여도 표시)
    for cat in REQUIRED_CATEGORIES:
        grouped.setdefault(cat, [])
    return grouped


def _build_prompt_for_message(category: str, index: int, total: int, msg: GmailMessage) -> str:
    # 본문 누락을 최소화하기 위해, 가능한 한 본문을 넉넉히 포함 (20,000자면 대부분의 TLDR 메일 수용 가능)
    body = (msg.body_text or "").strip()
    body = body[:20000] 
    received = msg.received_datetime_local.strftime("%Y-%m-%d %H:%M")

    return (
        "너는 최고의 한국어 번역가이자 기술 전문 필진이다.\n"
        f"아래는 오늘 받은 'TLDR {category}' 분야 뉴스레터 메일이다. ({index}/{total})\n\n"
        "**[수행 지침 - 절대 준수]**\n"
        "1) 모든 결과물은 한국어로 작성한다.\n"
        "2) 전문 용어, 제품명, 서비스명, 영문 키워드 등은 번역하지 말고 원문(English)을 그대로 유지한다.\n"
        "3) 위 2)의 용어가 처음 등장할 때, 반드시 옆에 괄호를 열고 해당 용어의 의미나 정체를 한국어로 1문장 내외로 설명하라.\n"
        "   - 예: Vercel(프론트엔드 배포 및 호스팅 플랫폼)\n"
        "4) **[중요: 누락 금지]** '전체 내용' 섹션은 절대로 요약하지 마라. 본문에 포함된 모든 뉴스 헤드라인, 상세 설명, 기술적 통찰을 **토큰 제한을 고려하지 말고 최대한 상세하게** 한국어로 옮겨야 한다.\n"
        "5) 본문에 있는 리스트 항목, 섹션 구분(Big Data, Engineering 등)을 그대로 유지하며 번역하라. 단 하나라도 기사를 빼먹는 것은 용납되지 않는다.\n"
        "6) 영어 문장을 단순히 직역하지 말고, 문맥에 맞는 자연스러운 한국어 문장으로 재구성하여 서술하라.\n\n"
        "**[출력 형식]**\n"
        f"- 분야: {category}\n"
        f"- 제목: {msg.subject}\n"
        "- 요약본: 이 뉴스레터 전체를 관통하는 핵심 요약 3~5줄.\n"
        "- 전체 내용: 본문의 모든 섹션과 개별 기사 내용을 하나도 빠짐없이 번역 및 상세 서술(분량 제한 없음).\n\n"
        "**[메일 데이터]**\n"
        f"- 수신: {received}\n"
        f"- 발신자: {msg.from_header}\n"
        f"- 스니펫: {msg.snippet}\n"
        f"- 본문 원문:\n{body}\n"
    )


def summarize_category_korean(category: str, messages: list[GmailMessage]) -> str:
    # 1순위: 환경변수 OPENAI_API_KEY
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    # 2순위: 현재 폴더의 OPENAI_API_KEY.text 파일 내용
    if not api_key:
        key_path = "OPENAI_API_KEY.text"
        if os.path.exists(key_path):
            try:
                with open(key_path, "r", encoding="utf-8") as f:
                    api_key = f.read().strip()
            except OSError:
                api_key = ""
    if not api_key:
        return (
            f"- 분야: {category}\n"
            "- 제목: (생성 불가)\n"
            "- 요약본: OPENAI_API_KEY가 설정되지 않아 요약을 생성할 수 없습니다.\n"
            "- 전체 내용: `OPENAI_API_KEY` 환경변수를 설정하거나 OPENAI_API_KEY.text 파일에 키를 넣고 다시 시도하세요.\n"
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    if not messages:
        return (
            f"- 분야: {category}\n"
            "- 제목: (없음)\n"
            "- 요약본: 이 분야에 해당하는 메일이 없습니다.\n"
            "- 전체 내용: (없음)\n"
        )

    outputs: list[str] = []
    sorted_msgs = sorted(messages, key=lambda x: x.internal_date)
    total = len(sorted_msgs)

    for idx, msg in enumerate(sorted_msgs, start=1):
        prompt = _build_prompt_for_message(category, idx, total, msg)
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": "You are a careful bilingual summarizer."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        text = (resp.choices[0].message.content or "").strip()
        outputs.append(text)

    return "\n\n".join(outputs).strip()


def summarize_all(grouped: dict[str, list[GmailMessage]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for cat, msgs in sorted(grouped.items(), key=lambda x: x[0].lower()):
        out[cat] = summarize_category_korean(cat, msgs)
    return out


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gmail TLDR 번역/요약기")
        self.geometry("1000x700")

        self.grouped: dict[str, list[GmailMessage]] = {}
        self.summaries: dict[str, str] = {}
        self._worker: threading.Thread | None = None
        self._queue: "queue.Queue[tuple[str, object]]" = queue.Queue()

        self._tts_worker: threading.Thread | None = None
        self._tts_active: bool = False

        self._build_ui()
        # 실행 직후 자동으로 "오늘 TLDR 메일"을 불러오며, 최초 실행이면 OAuth가 진행됩니다.
        self.after(200, self.on_refresh)
        self.after(150, self._poll_queue)

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="both", expand=True)

        toolbar = ttk.Frame(top)
        toolbar.pack(fill="x")

        self.refresh_btn = ttk.Button(toolbar, text="오늘 TLDR 메일 불러오기", command=self.on_refresh)
        self.refresh_btn.pack(side="left")

        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side="left", padx=10)

        progress_row = ttk.Frame(top)
        progress_row.pack(fill="x", pady=(8, 0))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(progress_row, orient="horizontal", mode="determinate", variable=self.progress_var)
        self.progress.pack(side="left", fill="x", expand=True)

        self.eta_var = tk.StringVar(value="")
        ttk.Label(progress_row, textvariable=self.eta_var, width=26, anchor="e").pack(side="left", padx=(10, 0))

        body = ttk.Frame(top)
        body.pack(fill="both", expand=True, pady=10)

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")

        ttk.Label(left, text="분야 목록").pack(anchor="w")
        self.listbox = tk.Listbox(left, width=25, height=25)
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_select_category)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        ttk.Label(right, text="번역/요약 결과").pack(anchor="w")

        text_frame = ttk.Frame(right)
        text_frame.pack(fill="both", expand=True)

        self.text = tk.Text(text_frame, wrap="word")
        self.text.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(text_frame, command=self.text.yview)
        scrollbar.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=scrollbar.set)

        bottom = ttk.Frame(top)
        bottom.pack(fill="x")

        self.tts_btn = ttk.Button(bottom, text="선택 분야 TTS 재생 (여성 목소리)", command=self.on_tts_play)
        self.tts_btn.pack(side="left", padx=(0, 8))

        self.tts_progress_var = tk.DoubleVar(value=0)
        self.tts_progress = ttk.Progressbar(bottom, orient="horizontal", mode="determinate", variable=self.tts_progress_var, maximum=100)
        self.tts_progress.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.tts_status_var = tk.StringVar(value="TTS 대기")
        ttk.Label(bottom, textvariable=self.tts_status_var, width=22, anchor="w").pack(side="left")

        self.copy_btn = ttk.Button(bottom, text="현재 내용 클립보드 복사", command=self.on_copy)
        self.copy_btn.pack(side="left", padx=(8, 0))

    def set_status(self, msg: str):
        self.status_var.set(msg)
        self.update_idletasks()

    def on_refresh(self):
        if self._worker and self._worker.is_alive():
            self.set_status("이미 처리 중입니다. 잠시만 기다려주세요.")
            return

        self.refresh_btn.configure(state="disabled")
        self.progress.configure(mode="determinate")
        self.progress_var.set(0)
        self.progress.configure(maximum=1)
        self.eta_var.set("")
        self.set_status("백그라운드에서 처리 시작...")

        def run():
            try:
                self._queue.put(("status", "Gmail에서 오늘 TLDR 메일 조회 중..."))
                msgs = fetch_today_tldr_messages()
                grouped = group_by_category(msgs)
                self._queue.put(("loaded", (msgs, grouped)))

                categories = [c for c, _ in sorted(grouped.items(), key=lambda x: x[0].lower())]
                total = len(categories)
                self._queue.put(("progress_init", total))

                self._queue.put(("status", f"메일 {len(msgs)}개 로드 완료. 요약 생성 중..."))
                summaries: dict[str, str] = {}
                start = time.perf_counter()
                done = 0
                for cat in categories:
                    self._queue.put(("status", f"요약 생성 중... ({done}/{total}) 현재: {cat}"))
                    summaries[cat] = summarize_category_korean(cat, grouped.get(cat, []))
                    done += 1
                    elapsed = time.perf_counter() - start
                    avg = elapsed / done if done else 0.0
                    remaining = max(0, total - done)
                    eta_s = int(avg * remaining)
                    self._queue.put(("progress", (done, total, eta_s, cat)))

                self._queue.put(("done", (grouped, summaries)))
            except Exception as e:
                self._queue.put(("error", str(e)))

        self._worker = threading.Thread(target=run, daemon=True)
        self._worker.start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "status":
                    self.set_status(str(payload))
                elif kind == "progress_init":
                    total = int(payload)
                    self.progress.configure(maximum=max(1, total))
                    self.progress_var.set(0)
                    self.eta_var.set("0/{} (ETA 계산중)".format(total) if total else "0/0")
                elif kind == "progress":
                    done, total, eta_s, cat = payload  # type: ignore[misc]
                    self.progress_var.set(float(done))
                    eta_mm = eta_s // 60
                    eta_ss = eta_s % 60
                    if total:
                        pct = int((done / total) * 100)
                        self.eta_var.set(f"{done}/{total} ({pct}%) ETA {eta_mm:02d}:{eta_ss:02d}")
                    else:
                        self.eta_var.set("0/0")
                elif kind == "loaded":
                    _msgs, grouped = payload  # type: ignore[misc]
                    self.grouped = grouped
                    self.listbox.delete(0, tk.END)
                    # 고정 분야 순서대로 표시 (메일이 0개여도 목록에 포함)
                    for cat in REQUIRED_CATEGORIES:
                        self.listbox.insert(tk.END, f"{cat} ({len(self.grouped[cat])})")
                elif kind == "done":
                    grouped, summaries = payload  # type: ignore[misc]
                    self.grouped = grouped
                    self.summaries = summaries
                    self.refresh_btn.configure(state="normal")
                    self.set_status("요약 생성 완료. 분야를 선택하세요.")
                    self.eta_var.set("완료")

                    if self.grouped:
                        self.listbox.selection_clear(0, tk.END)
                        self.listbox.selection_set(0)
                        self.on_select_category()
                    else:
                        self.text.delete("1.0", tk.END)
                        self.text.insert(tk.END, "오늘 조건에 맞는 TLDR 메일이 없습니다.\n")
                elif kind == "error":
                    self.refresh_btn.configure(state="normal")
                    self.set_status("오류 발생")
                    self.eta_var.set("")
                    messagebox.showerror("오류", str(payload))
                elif kind == "tts_status":
                    self.tts_status_var.set(str(payload))
                elif kind == "tts_progress":
                    self.tts_progress_var.set(float(payload))
                elif kind == "tts_done":
                    self.tts_status_var.set("TTS 재생 완료")
                    self.tts_progress_var.set(100)
                elif kind == "tts_error":
                    self.tts_status_var.set("TTS 오류")
                    self.tts_progress_var.set(0)
                    messagebox.showerror("TTS 오류", str(payload))
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    def _selected_category_key(self) -> str | None:
        sel = self.listbox.curselection()
        if not sel:
            return None
        label = self.listbox.get(sel[0])
        # "Dev (3)" -> "Dev"
        m = re.match(r"^(.*?)\s*\(\d+\)\s*$", label)
        if not m:
            return label.strip()
        return m.group(1).strip()

    def on_select_category(self, *_):
        cat = self._selected_category_key()
        if not cat:
            return
        content = self.summaries.get(cat) or ""
        if not content:
            content = f"(요약 결과가 없습니다) 분야={cat}"
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, content.strip() + "\n")

    def on_copy(self):
        txt = self.text.get("1.0", tk.END).strip()
        self.clipboard_clear()
        self.clipboard_append(txt)
        self.set_status("클립보드에 복사했습니다.")

    def on_tts_play(self):
        if self._tts_active:
            messagebox.showinfo("TTS", "현재 TTS가 재생 중입니다. 잠시 기다려주세요.")
            return

        text = self.text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("TTS", "재생할 텍스트가 없습니다. 먼저 분야를 선택하고 요약을 생성하세요.")
            return

        self._tts_worker = threading.Thread(target=self._tts_worker_fn, args=(text,), daemon=True)
        self._tts_worker.start()

    def _tts_worker_fn(self, text: str):
        self._tts_active = True
        self._queue.put(("tts_status", "TTS 생성/재생 중..."))
        self._queue.put(("tts_progress", 0))

        try:
            try:
                import pyttsx3
            except ImportError as e:
                self._queue.put(("tts_error", "pyttsx3 모듈을 찾을 수 없습니다. \npip 설치: pip install pyttsx3"))
                self._tts_active = False
                return

            engine = pyttsx3.init()
            voices = engine.getProperty("voices")
            selected_voice = None
            for v in voices:
                low = (v.name or "").lower() + " " + (v.id or "").lower()
                if "female" in low or "여성" in low or "female" in (v.languages or []):
                    selected_voice = v.id
                    break
            if selected_voice is None and voices:
                selected_voice = voices[0].id

            if selected_voice is not None:
                engine.setProperty("voice", selected_voice)

            words = text.split()
            total = len(words) if words else 1

            def on_word(name, location, length):
                # location: 현재 단어 인덱스
                pct = min(100, int((location / total) * 100))
                self._queue.put(("tts_progress", pct))

            engine.connect("started-word", on_word)
            engine.say(text)
            engine.runAndWait()

            self._queue.put(("tts_progress", 100))
            self._queue.put(("tts_done", None))
        except Exception as e:
            self._queue.put(("tts_error", str(e)))
        finally:
            self._tts_active = False


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()