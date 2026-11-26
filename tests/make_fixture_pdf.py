"""테스트용 공고문 PDF 생성기 (한 번만 실행해 fixtures/notice_jeonse.pdf를 만든다).

실제 '제51차 장기전세주택 입주자 모집공고' 게시글의 문장 구조를 본떠 만든 3쪽짜리 PDF.
실제 공고문 PDF를 저장소에 넣지 않으려고(저작권·용량) 직접 생성한다.
실행: python tests/make_fixture_pdf.py  (reportlab 필요. 폰트 파일 없이 내장 한글 CID 폰트 사용)
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))

PAGES = [
    [
        "제51차 장기전세주택 입주자 모집공고",
        "서울주택도시개발공사",
        "Ⅰ. 공급개요",
        "■ 공급호수: 총 1,381세대(예비입주자 모집 포함)",
        "■ 공급위치: 강동구 고덕강일, 강서구 마곡 등 32개 단지",
        "Ⅱ. 신청자격",
        "모집공고일(2026.08.31.) 기준 서울특별시에 거주하는 성년자인 무주택세대구성원",
        "※ 단, 상계장암지구는 의정부시 거주자 신청가능",
    ],
    [
        "Ⅲ. 소득 및 자산 보유 기준",
        "60㎡ 이하: 도시근로자 월평균소득 100% 이하",
        "총자산 3억 4,500만원 이하, 자동차 3,803만원 이하",
        "Ⅳ. 입주자 선정 순위",
        "1순위: 해당 주택 건설지역 거주자로 청약통장 24회 이상 납입",
        "2순위: 청약통장 6회 이상 납입",
    ],
    [
        "Ⅴ. 공급일정",
        "청약신청 [1순위] 2026. 9. 14.(월) 10:00 ~ 2026. 9. 15.(화) 17:00",
        "청약신청 [2순위] 2026. 9. 16.(수) 10:00 ~ 17:00",
        "서류심사대상자 발표: 2026. 10. 14.(수) 16:00 이후",
        "당첨자 발표: 2027. 03. 05.(금) 16:00 이후",
    ],
]

out = Path(__file__).parent / "fixtures" / "notice_jeonse.pdf"
c = canvas.Canvas(str(out), pagesize=A4)
c.setTitle("제51차 장기전세주택 입주자 모집공고 (테스트용)")
for lines in PAGES:
    y = 800
    for ln in lines:
        c.setFont("HYSMyeongJo-Medium", 11)
        c.drawString(50, y, ln)
        y -= 22
    c.showPage()
c.save()
print(out, out.stat().st_size)
