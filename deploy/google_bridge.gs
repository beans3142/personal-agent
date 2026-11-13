// Daevis Google bridge: runs as the owner inside their Google account.
// Deployed as a web app (execute as me). Every call must carry KEY.
const KEY = '__DAEVIS_KEY__';
const FOLDER = 'Daevis';

function doPost(e) {
  let body;
  try { body = JSON.parse(e.postData.contents); } catch (err) { return reply({ok: false, error: 'bad json'}); }
  if (!body || body.key !== KEY) return reply({ok: false, error: 'forbidden'});
  try {
    if (body.action === 'calendar_list') return reply({ok: true, events: listEvents(body)});
    if (body.action === 'calendar_add') return reply({ok: true, event: addEvent(body)});
    if (body.action === 'drive_upload') return reply({ok: true, file: upload(body)});
    return reply({ok: false, error: 'unknown action'});
  } catch (err) {
    return reply({ok: false, error: String(err && err.message || err)});
  }
}

function reply(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}

function view(ev) {
  return {id: ev.getId(), title: ev.getTitle(), start: ev.getStartTime().toISOString(), end: ev.getEndTime().toISOString(),
          all_day: ev.isAllDayEvent(), location: ev.getLocation()};
}

function listEvents(body) {
  const start = new Date(body.start), end = new Date(body.end);
  if (isNaN(start) || isNaN(end) || end <= start) throw new Error('bad range');
  return CalendarApp.getDefaultCalendar().getEvents(start, end).slice(0, 50).map(view);
}

function addEvent(body) {
  const cal = CalendarApp.getDefaultCalendar();
  const options = {location: body.location || '', description: body.description || ''};
  let ev;
  if (body.all_day) {
    const day = new Date(body.start);
    if (isNaN(day)) throw new Error('bad date');
    ev = cal.createAllDayEvent(body.title, day, options);
  } else {
    const start = new Date(body.start), end = new Date(body.end);
    if (isNaN(start) || isNaN(end) || end <= start) throw new Error('bad range');
    ev = cal.createEvent(body.title, start, end, options);
  }
  return view(ev);
}

function upload(body) {
  const folders = DriveApp.getFoldersByName(FOLDER);
  const folder = folders.hasNext() ? folders.next() : DriveApp.createFolder(FOLDER);
  const blob = Utilities.newBlob(Utilities.base64Decode(body.data), body.mime || 'application/octet-stream', body.name);
  const file = folder.createFile(blob);
  return {id: file.getId(), name: file.getName(), url: file.getUrl(), size: file.getSize()};
}
