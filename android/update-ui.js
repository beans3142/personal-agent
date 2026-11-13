(() => {
  if (document.getElementById('appVersion')) return;
  const api = () => window.pywebview.api;
  const badge = document.createElement('span');
  badge.id = 'appVersion'; badge.style.cssText = 'font-size:11px;color:#9fb3c8;white-space:nowrap;margin:0 8px';
  document.getElementById('openSettings').before(badge);
  const box = document.createElement('section');
  box.style.cssText = 'margin:18px 0;padding:14px;border:1px solid #254252;border-radius:10px';
  box.innerHTML = '<h3 style="margin:0 0 8px">앱 버전 · 업데이트</h3><p class="note" id="updateVersion"></p><p class="note" id="updateState"></p><button class="chip" id="checkUpdate">지금 업데이트 확인</button> <button class="chip" id="allowUpdate">설치 허용 설정</button>';
  document.getElementById('drawer').insertBefore(box, document.getElementById('drawer').children[1]);
  const labels = { not_checked:'아직 확인하지 않음', checking:'새 버전 확인 중…', up_to_date:'최신 버전', permission_required:'설치 허용 필요', waiting_for_idle:'폰 작업이 끝나면 설치', downloading:'새 버전 다운로드 중…', installing:'설치 중…', awaiting_confirmation:'알림에서 설치 확인 필요', failed:'업데이트 확인 또는 설치 실패 — 다시 확인해 주세요' };
  async function refresh() {
    try {
      const s = await api().update_status();
      badge.textContent = 'v' + s.version_name + ' · ' + s.installed;
      document.getElementById('updateVersion').textContent = '현재 ' + s.version_name + ' (빌드 ' + s.installed + ') · 최신 빌드 ' + (s.latest || '확인 전');
      document.getElementById('updateState').textContent = (labels[s.state] || s.state) + ' · 자동 확인 1분 간격';
      document.getElementById('allowUpdate').hidden = s.install_allowed;
    } catch (_) { document.getElementById('updateState').textContent = '버전 정보를 읽지 못했어요'; }
  }
  document.getElementById('checkUpdate').onclick = async () => { await api().check_update(); refresh(); };
  document.getElementById('allowUpdate').onclick = () => api().allow_updates();
  refresh();
  setInterval(() => { if (!document.hidden && document.getElementById('drawer').classList.contains('open')) refresh(); }, 2000);
})();
