# 카톡을 서버에서 돌리려고 Waydroid를 버리고 ReDroid로 갔다

> 2026-11-02 · `9d0e7e9` `bdb7814` `2bcd0ea` `08c7940` · x86과 ARM 양쪽에서 죽던 Android를 ReDroid 13 컨테이너로 바꿔 띄웠다

## 무엇이 문제였나

카카오톡은 웹으로 다른 사람에게 메시지를 보낼 수 없고, 배달의민족은 앱 중심이다. 그런데 Agent는 "서버에서만 돈다"는 조건이 있다. 서버 안에 Android가 필요했다.

## 왜 그런 일이 생겼나

처음 시도한 것은 Waydroid다. x86 서버에서는 Play 화면이 비었고 ARM 서버(t4g.medium)에서는 소프트웨어 렌더러로 돌다가 계속 죽었다. Mesa 설정을 바꾸고 다른 이미지도 시험했지만 안정되지 않았다.

## 고를 수 있던 방법

| 방법 | 판단 |
| --- | --- |
| PC 카카오톡 | 휴대폰과 중복 로그인 문제가 있어 버림 |
| Waydroid 설정을 계속 조정 | x86, ARM 양쪽에서 안정되지 않아 버림 |
| 내 휴대폰을 실행 기기로 쓰기 | 앱이 에뮬레이터를 거부할 때를 위한 대안으로 남김 |
| ReDroid 13 Docker 이미지 | 고름 |

## 바꾼 것

- ARM 서버 안에 공식 ReDroid 13 컨테이너를 두고 이미지는 digest로 고정했다. 같은 이름의 이미지가 바뀌어도 서버가 깨지지 않게 하려는 것이다.
- ADB는 서버 내부 주소로만 열었다. `2bcd0ea`에서 ReDroid의 ADB 주소를 기기 번호로 받게 했다.
- 앱은 uiautomator2로 화면 구조를 읽어 조작한다. 카카오톡은 태블릿(보조 기기)으로 로그인해서 휴대폰과 같이 쓴다.
- Google Play 서비스가 없어서 앱은 공식 설치본을 그대로 옮겨 깔았다.
- `bdb7814`: Android를 서비스로 띄워 서버가 켜질 때 같이 뜨게 했다.

## 틀린 지점

재부팅 뒤 Android가 재시작을 반복한 적이 있다. 원인은 `binder_linux` 커널 모듈이 부팅 때 올라오지 않은 것이었다. 서비스 시작 전에 모듈을 적재하도록 넣었다(`deploy/android.service`의 `ExecStartPre`). `08c7940`에 재시작 후에도 유지되는 것을 확인한 기록을 남겼다.

## 아직 모르는 것

4GB 메모리에 Android, n8n, Hermes를 같이 올렸을 때 남는 메모리는 설치 직후 잰 약 2.2GB뿐이다. 동시에 일을 시켰을 때 버티는지는 재지 않았다. 카카오톡 로그인이 Android 재부팅이나 장기 세션 만료 뒤에도 남는지도 모른다.

## 셀프 인터뷰

**왜 휴대폰을 직접 쓰지 않았나요?**
휴대폰을 쓰면 폰이 늘 켜져 있고 연결돼 있어야 합니다. 서버만으로 도는 것이 처음 조건이었습니다. 다만 앱이 에뮬레이터를 거부하면 휴대폰 노드로 갈 수 있도록 기기 노드 중계는 따로 만들어 뒀습니다.

**이미지를 digest로 고정한 이유는요?**
태그는 같은 이름으로 내용이 바뀔 수 있습니다. 한 번 맞춘 Android 환경이 업데이트 하나로 깨지면 원인을 찾기 어렵습니다.

**이 작업에서 배운 점은요?**
설정을 계속 바꿔 보는 데에도 한도를 정해야 한다는 점입니다. Waydroid를 붙잡고 있던 시간보다, 다른 이미지로 갈아타서 바로 뜬 시간이 훨씬 짧았습니다.

## 산출물

- 커밋: `9d0e7e9` Add Android start script and remove probes
- 커밋: `bdb7814` Keep sessions alive and start Android as a service
- 커밋: `2bcd0ea` Fix Android target selection and support ReDroid serial
- 커밋: `08c7940` Record verified Android restart persistence
