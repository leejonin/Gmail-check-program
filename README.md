# Gmail-check-program

# Gmail TLDR 번역/요약기

## 이 프로그램이 하는 일
- **오늘 받은 메일** 중 제목이 `TLDR <분야>` 로 시작하는 Gmail 메일을 모두 가져옵니다.
- `TLDR` 다음 단어를 **분야**로 보고, 분야별로 묶습니다. (예: `TLDR Dev` → `Dev`)
- 분야별로 **한국어 요약/번역본**을 생성합니다.
  - 전문 용어/제품명/약어/코드/서비스명/영문 키워드는 **번역하지 않고 원문 그대로 유지**
  - 해당 용어가 나오면 옆에 `(한국어로 1문장 설명)`을 붙입니다.
- tkinter UI에서 분야 목록과 결과(스크롤 텍스트)를 볼 수 있습니다.

## 준비물(필수/권장)
- **필수**: Google 계정, Gmail 사용 가능
- **필수**: Google Cloud Console에서 Gmail API 활성화 + OAuth 데스크톱 클라이언트 JSON(`credentials.json`)
- **권장**: OpenAI API Key (요약/번역 생성용)

## 설치
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Google Cloud 설정(처음 1회)
1) Google Cloud Console에서 프로젝트 생성/선택
2) Gmail API 활성화
   - Google Cloud Console → **APIs & Services** → **Library**
   - `Gmail API` 검색 → **Enable**
3) OAuth 동의 화면(Consent screen) 설정
   - **APIs & Services** → **OAuth consent screen**
   - 개인 계정이면 보통 **External** 선택
   - App name, 이메일 등 필수 항목 입력 후 저장
4) OAuth 2.0 클라이언트 만들기(Desktop app)
   - **APIs & Services** → **Credentials** → **Create Credentials** → **OAuth client ID**
   - Application type: **Desktop app**
   - 생성 후 해당 항목에서 **Download JSON** 클릭
5) 다운로드한 JSON 파일을 이 폴더에 저장
   - 파일명을 **정확히** `credentials.json` 으로 저장
   - 주의: `credentials.json`이 **비어있거나(0바이트) JSON이 깨져있으면** OAuth가 시작되지 않습니다.

## OpenAI 키 설정(요약/번역을 하려면)
아래 중 하나만 하면 됩니다.

### 방법 A) 환경변수로 설정(권장)
PowerShell(현재 세션에만 적용):
```powershell
$env:OPENAI_API_KEY="YOUR_KEY"
```

### 방법 B) 파일로 설정(간단)
이 폴더에 `OPENAI_API_KEY.text` 파일을 만들고 **1줄에 키만** 넣으세요.
예:
```
sk-...여기에_키...
```

## 실행
```bash
python .\MianCode.py
```

## GitHub 업로드 가이드 (개인정보 제거)
- `credentials.json`, `token.json`, `OPENAI_API_KEY.text`는 절대 커밋하지 마세요.
- 코드에서 API 키를 직접 하드코딩하지 마세요.
- 예시값을 그대로 올릴 경우 자동으로 재설정 하세요:
  - `OPENAI_API_KEY.text` 예시: `put your API key here`
  - `credentials.json` 예시: 개인 정보가 포함된 원본을 올리지 말고, `credentials.template.json`을 만들어서 (빈 값) 공유

### 중요: 파일 생성/세팅 예시
1) `.gitignore`에 아래 추가
```gitignore
credentials.json
token.json
OPENAI_API_KEY.text
.venv/
__pycache__/
```

2) 환경변수 예시 (PowerShell):
```powershell
$env:OPENAI_API_KEY="put your API key here"
```

3) 파일 예시 (기본값)
- `OPENAI_API_KEY.text`:
  - `put your API key here`

- `credentials.template.json`:
  - 실제 응용 시 Google Cloud에서 다운로드받아 `credentials.json`에 저장
  - 템플릿에는 민감 정보 제거(빈 스트링 또는 주석으로 대체)

## 첫 실행 흐름(정상 동작)
- 앱을 실행하면 **자동으로** “오늘 TLDR 메일 불러오기”가 실행됩니다.
- 처음 실행이거나 `token.json`이 없으면 브라우저가 열리며 OAuth 동의가 진행됩니다.
- 동의가 끝나면 이 폴더에 `token.json` 이 생성되고, 다음부터는 자동으로 재사용됩니다.

## 동작 규칙(요구사항 반영)
- 오늘 날짜에 해당되는 메일만 조회
- 제목이 `TLDR <분야>` 로 시작하는 메일을 모두 불러오고, `<분야>` 기준으로 묶음
- 분야별로 한국어 요약본 생성
- 용어(영문/약어/코드/제품명 등)는 번역하지 않고, 해당 용어 옆에 `(간단 설명 문장)`을 붙이도록 프롬프트로 강제
- tkinter UI에 스크롤 가능한 텍스트 박스로 결과 표시

## 자주 발생하는 오류/해결
### `Expecting value: line 1 column 1 (char 0)`
- 보통 `credentials.json` 또는 `token.json`이 **빈 파일**이거나 **JSON 형식이 깨진 경우**입니다.
- 해결:
  - `credentials.json`이 정상 OAuth Desktop JSON인지 확인(0바이트면 다시 다운로드)
  - `token.json`이 깨졌다면 삭제 후 재실행(자동으로 OAuth 재진행)

### OAuth 브라우저 창이 안 뜸
- `credentials.json`이 없거나 비어있으면 OAuth가 시작되지 않습니다.
- Windows 방화벽/보안 프로그램이 로컬 리디렉션을 막는 경우도 있습니다(이때는 오류 메시지가 뜹니다).

