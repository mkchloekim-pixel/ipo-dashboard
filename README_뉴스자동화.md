# 뉴스 자동화 설정 가이드

## 1. 파일 구성
```
index.html          ← 대시보드 (news.json을 fetch해서 뉴스 표시)
news.json           ← GitHub Actions가 매일 자동 갱신
fetch_news.py       ← 뉴스 수집 스크립트
.github/workflows/
  update_news.yml   ← 매일 07:00 KST 자동 실행
```

## 2. 네이버 뉴스 API 키 발급
1. https://developers.naver.com 접속 → 애플리케이션 등록
2. 검색 API 선택 → Client ID, Client Secret 발급

## 3. GitHub Secrets 등록
Repository → Settings → Secrets and variables → Actions → New repository secret
- `NAVER_CLIENT_ID` : 네이버 Client ID
- `NAVER_CLIENT_SECRET` : 네이버 Client Secret

## 4. 배포 순서
1. 이 폴더의 파일들을 GitHub 레포에 업로드
2. `ipo_dashboard_integrated.html` → `index.html` 로 이름 변경 후 업로드
3. Vercel 또는 GitHub Pages로 배포
4. Actions 탭 → "뉴스 자동 수집" → Run workflow 로 즉시 테스트

## 5. 동작 방식
- 매일 07:00(KST) 자동 실행 → 16개사 뉴스 수집 → news.json 갱신 → 자동 배포
- 기업 프로파일 클릭 시 news.json 로드 → 실시간 뉴스 표시
- 수집 실패 시 기존 큐레이션 뉴스로 자동 폴백 (화면이 비지 않음)
- 수동 실행: Actions 탭 → Run workflow
