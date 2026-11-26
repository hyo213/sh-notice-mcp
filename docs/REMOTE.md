# 휴대폰에서 쓰기: 원격 MCP 서버

맥 버전은 Claude 데스크톱 앱이 맥에서 직접 실행합니다. 그래서 맥이 켜져 있어야 쓸 수 있습니다.
이 문서는 같은 기능 일부를 **인터넷에 띄워 claude.ai의 '사용자 지정 커넥터'로 연결**하는 방법입니다.
연결해 두면 휴대폰 Claude 앱에서도 맥 없이 쓸 수 있습니다. Pro 플랜에서도 됩니다.

## 원격 버전에 있는 것과 없는 것

| 있음 | 없음 (맥 버전에서만) |
|---|---|
| 공고 찾기·최근 공고·접수 상태 (`find_notices`, `recent_notices`) | 내 조건 저장·수정 (`get_my_profile`, `update_my_profile`) |
| 게시글·공고문 PDF 읽기 (`get_notice`, `read_notice_pdf`), 일정 뽑기 (`extract_schedule`) | 캘린더 등록 (`confirm_registration` 등) |
| 비용표 (`get_housing_costs`): 가용 현금·생애최초·예상 금리는 **인자로** 받음 | 접수번호 저장 (`save_receipt_numbers`, `my_receipts`) |
| 당첨 조회 (`check_results`): 접수번호를 **인자로** 받고 저장하지 않음 | |

원격 서버는 개인정보를 저장하지 않습니다. 소득·가용 현금·접수번호는 대화에서 그때그때 넘기고, 서버에 남는 것은 공고문 PDF 텍스트 캐시뿐입니다.

## 1. 토큰 만들기

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

이 값이 서버의 비밀번호입니다. 모든 요청에 `Authorization: Bearer <토큰>` 헤더가 있어야 하고, 없으면 401로 막습니다.
토큰을 저장소에 커밋하지 마세요.

## 2. Fly.io에 배포 (예시)

[Fly.io](https://fly.io) 계정과 `flyctl`이 필요합니다. 작게 쓰면 무료 범위이거나 월 몇 달러 수준입니다. 요금은 Fly.io에서 확인하세요.

```bash
cd sh-notice-mcp
fly launch --no-deploy            # fly.toml의 app 이름을 정함
fly secrets set SH_REMOTE_TOKEN=<1에서 만든 토큰>
fly deploy
```

배포가 끝나면 주소는 `https://<app 이름>.fly.dev/mcp` 입니다.
다른 곳(Render, Railway, 자기 서버 등)도 Dockerfile 그대로 쓸 수 있습니다. 포트는 `PORT` 환경변수(기본 8080)를 따릅니다.
Claude는 표준 포트(443)의 HTTPS 주소에만 연결하므로, `:8443` 같은 포트가 붙은 주소는 쓸 수 없습니다.

## 3. claude.ai에 커넥터 추가

1. claude.ai(웹이나 데스크톱 앱) → 설정 → 커넥터 → **사용자 지정 커넥터 추가**
2. URL: `https://<app 이름>.fly.dev/mcp`
3. 인증은 'No sign in'(OAuth 없음)으로 두고, **Request headers**에 `Authorization` = `Bearer <토큰>` 을 넣습니다.
4. 저장하면 휴대폰 Claude 앱에도 같은 커넥터가 보입니다. 대화창의 "+" → 커넥터에서 켭니다.

## 4. 쓰는 법

- "최근 SH 공고 있어?"
- "고덕강일 공고 비용표 보여줘. 가용 현금 1.3억, 생애최초, 금리 2.5%로"
- "고덕강일 당첨 확인해줘. 접수번호 511111, 522222"

## 보안 메모

- 서버는 SH 공개 게시판만 읽습니다. 쓰기·삭제 도구가 없습니다.
- 토큰이 새면 `fly secrets set SH_REMOTE_TOKEN=<새 토큰>`으로 바꾸고 커넥터 헤더도 바꾸세요.
- SH 사이트에 부담을 주지 않도록 요청 간격 1초를 지키고 게시판 1페이지만 읽습니다.
