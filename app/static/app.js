'use strict';
const $ = s => document.querySelector(s);
const state = {me:null, config:null, view:'calendar', day:null, plans:[], events:[], poll:null, chatPending:null};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const days = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
const kinds = {medication:'Препарат',vitamin:'Витамины',food:'Питание',other:'Личное'};
const symbols = {medication:'✚',vitamin:'✦',food:'◔',other:'◇'};
const fmt = (value, opts={}) => new Intl.DateTimeFormat('ru-RU',{timeZone:state.me?.timezone || 'Europe/Moscow',...opts}).format(new Date(value));
function localDay(value=new Date()) { const p = new Intl.DateTimeFormat('en-CA',{timeZone:state.me?.timezone || 'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(value)); return ['year','month','day'].map(k=>p.find(x=>x.type===k).value).join('-'); }
const shiftDay = (day,n) => new Date(new Date(day+'T12:00:00Z').getTime()+n*86400000).toISOString().slice(0,10);
const humanDate = day => fmt(day+'T12:00:00Z',{day:'numeric',month:'long'});
const btn = (action,label,cls='',attrs='') => `<button type="button" class="${cls}" data-action="${action}" ${attrs}>${label}</button>`;
const chatActions={medications:['nav-medications','Мои препараты'],documents:['nav-documents','Анализы и справки'],calendar:['nav-calendar','Открыть календарь'],plans:['plans','Открыть назначения'],booking:['booking','Выбрать время приёма'],personal_event:['personal','Добавить своё событие']};
function chatSuggestion(action){const item=chatActions[action];return item?`<div class="chat-suggestion">${btn(item[0],item[1])}</div>`:'';}
function toast(text,error=false) { const el=document.createElement('div');el.className='toast'+(error?' error':'');el.textContent=text;$('#toasts').append(el);while($('#toasts').children.length>2)$('#toasts').firstElementChild.remove();setTimeout(()=>el.remove(),6500); }
async function api(path,method='GET',body) {
  const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-Meditron-Request':'1'},...(body!==undefined?{body:JSON.stringify(body)}:{})});
  const raw=await response.text();
  let data;
  try{data=raw?JSON.parse(raw):{};}catch{throw new Error(response.ok?'Не удалось прочитать ответ сервера. Повторите запрос.':'Сервис не смог обработать запрос. Текст сообщения сохранён — попробуйте отправить его ещё раз.');}
  if(!response.ok) { const detail=Array.isArray(data.detail)?data.detail.map(e=>`${e.loc.slice(1).join('.')}: ${e.msg.replace('Value error, ','')}`).join('\n'):data.detail; throw new Error(detail || 'Не удалось выполнить действие'); }
  return data;
}
function openModal(title,body) { $('#modal-content').innerHTML=`<div class="modal-head"><h2>${esc(title)}</h2>${btn('close','×','icon-button','aria-label="Закрыть"')}</div><div class="modal-body">${body}</div>`; if(!$('#modal').open)$('#modal').showModal(); }
function closeModal(){ $('#modal').close(); }
function empty(title,text,action='') {return `<div class="empty"><div class="empty-symbol">◷</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${action}</div>`;}

const documentKinds={all:'Все',analysis:'Анализы',certificate:'Справки'};
state.view='home'; state.courses=[]; state.documents=[]; state.docFilter='all'; state.docSearch=''; state.selectedDocument=null; state.courseFilter=null;
const logo=()=>`<div class="brand" aria-label="НейрON"><span class="brand-orb" aria-hidden="true"></span><span>Нейр<span class="brand-on">ON</span></span></div>`;
const heading=(title,text,actions='')=>`<div class="heading"><div><h1>${esc(title)}</h1><p>${esc(text)}</p></div>${actions}</div>`;
const tabs=()=>`<div class="tabs">${[['calendar','Календарь'],['medications','Мои препараты'],['treatment','План лечения']].map(([v,n])=>btn('nav-'+v,n,state.view===v?'selected':'')).join('')}</div>`;
function loginScreen(){
 $('#app').innerHTML=`<div class="welcome">${logo()}<div class="welcome-card"><span class="pill taken">Личный кабинет пациента</span><h1>Забота о здоровье<br>в вашем ритме</h1><p>План лечения, календарь приёмов и документы — всё в одном месте.</p>${state.config.demo?btn('login-patient','Открыть демоверсию','primary'):'<p>Демонстрационный вход отключён.</p>'}<p class="small muted">Демонстрация сервиса. Используйте тестовые данные.</p></div></div>`;
}
function shell(){
 const nav=[['home','Сегодня'],['chat','Чат'],['calendar','Календарь'],['documents','Документы'],['appointments','Записи'],['history','История здоровья']];
 $('#app').innerHTML=`<div class="shell"><aside class="sidebar">${logo()}<nav aria-label="Основное меню">${nav.map(([v,n])=>btn('nav-'+v,n,'nav-item '+(state.view===v||(v==='calendar'&&['medications','treatment'].includes(state.view))?'active':''))).join('')}</nav><button class="profile" data-action="profile"><span class="avatar"></span><span><strong>${esc(state.me.name.split(' ')[0])}</strong><small>Профиль и настройки</small></span></button></aside><main class="content" id="content"></main></div>`;
}
async function load(){
 state.config=await api('/config');try{state.me=await api('/me');}catch{state.me=null;}
 clearInterval(state.poll);
 if(state.me?.role!=='patient'){state.me=null;loginScreen();return;}
 state.day=state.day||localDay();await render();pollReminders();state.poll=setInterval(pollReminders,30000);
}
async function render(){
 shell();$('#content').innerHTML='<div class="loading">Загружаем…</div>';
 const views={home:renderHome,calendar:renderCalendar,chat:renderChat,medications:renderMedications,treatment:renderTreatment,documents:renderDocuments,appointments:renderAppointments,history:renderHistory};
 await (views[state.view]||renderHome)();
}
function courseProgress(c){
 return `<div class="course-progress"><p>${c.total===null?`Без даты окончания · отмечено ${c.taken} приёмов`:`Курс выполнен на ${c.percent}% · ${c.taken} из ${c.total} приёмов`}</p>${c.total!==null?`<progress max="100" value="${c.percent}" aria-label="Прогресс курса ${esc(c.title)}">${c.percent}%</progress>`:''}${!c.active?'<small class="muted">Повторение остановлено</small>':''}</div>`;
}
function demoBanner(){return `<div class="banner"><div><h3>Назначение от вашего врача</h3><p>Получите подготовленный пример назначения из внешней клиники.</p></div>${btn('simulate','Получить демоназначение','primary')}</div>`;}
async function renderHome(){
 const today=localDay(); const [events,courses,bookings,plans]=await Promise.all([api(`/calendar?start=${today}&end=${today}`),api('/courses'),api('/booking'),api('/plans')]);
 state.courses=courses;state.plans=plans;
 const first=courses.find(c=>c.active); const upcoming=bookings.filter(b=>b.status==='booked'&&b.starts_at.slice(0,10)===today);
 const visible=events.filter(e=>e.status!=='cancelled');
 $('#content').innerHTML=`<div class="today-page">`+heading('Сегодня',fmt(today+'T12:00:00Z',{weekday:'long',day:'numeric',month:'long'}))+`
 <section class="today-shortcuts"><div><h2>Здоровье под рукой</h2><p>Записи к врачу, препараты и документы — в одном месте.</p></div><div class="today-actions">${btn('booking','<span class="shortcut-symbol" aria-hidden="true">＋</span>Записаться к врачу')}${btn('nav-medications','<span class="shortcut-symbol" aria-hidden="true">◷</span>Мои препараты')}${btn('nav-documents','<span class="shortcut-symbol" aria-hidden="true">≡</span>Анализы и справки')}</div></section>
 <div id="reminders"></div>${!plans.length?demoBanner():plans.some(p=>!p.scheduled)?`<div class="banner"><div><h3>Новое назначение</h3><p>${esc(plans.find(p=>!p.scheduled).title)}</p></div>${btn('nav-treatment','Открыть план лечения')}</div>`:''}
 <div class="home-grid"><section class="card today-schedule"><div class="today-section-heading"><h2>Расписание на день</h2>${btn('nav-calendar','Календарь ↗','text-button')}</div>${visible.length?`<p class="today-count">Отмечено приёмов: ${visible.filter(e=>e.status==='taken').length} из ${visible.length}</p>`:''}${visible.map(eventCard).join('')}${upcoming.map(b=>`<article class="event"><div class="event-time">${esc(b.starts_at.slice(11,16))}</div><div><h3>Приём · ${esc(b.specialty)}</h3><p>${esc(b.doctor)}</p>${btn('booking','Открыть запись','text-button')}</div></article>`).join('')}${!visible.length&&!upcoming.length?empty('На сегодня всё свободно','Добавьте курс или своё событие в календарь.',btn('personal','Добавить событие')):''}</section>
 <aside class="card today-treatment"><span class="today-eyebrow">Ежедневная забота</span><h2>План лечения</h2>${first?`<div class="today-course"><h3>${esc(first.title)}</h3>${courseProgress(first)}</div>${btn('nav-medications','Все курсы ↗','text-button')}`:'<p class="muted">Добавьте курс, чтобы видеть свой прогресс.</p>'+btn('nav-treatment','Открыть назначения','text-button')}</aside></div></div>`;
}
async function renderCalendar(){
 const weekday=(new Date(state.day+'T12:00:00Z').getUTCDay()+6)%7, first=shiftDay(state.day,-weekday),last=shiftDay(first,6);
 const [events,courses,observations]=await Promise.all([api(`/calendar?start=${first}&end=${last}`),api('/courses'),api('/observations')]);state.events=events;state.courses=courses;
 const visible=events.filter(e=>e.status!=='cancelled'&&(!state.courseFilter||e.series_id===state.courseFilter));const selected=visible.filter(e=>localDay(e.scheduled_at)===state.day);
 $('#content').innerHTML=heading('Календарь','Календарь приёма препаратов и наблюдений',`<div class="row">${btn('notifications','Напоминания')}${btn('personal','＋ Добавить событие','primary')}</div>`)+tabs()+`<div id="reminders"></div>${state.courseFilter?`<div class="filter-note">Расписание одного курса ${btn('clear-course','Показать все','text-button')}</div>`:''}<div class="calendar-layout"><section class="card week-card"><div class="row space calendar-toolbar"><h2>${humanDate(first)} — ${humanDate(last)}</h2><div class="row">${btn('previous-week','‹','icon-button','aria-label="Предыдущая неделя"')}${btn('today','Сегодня')}${btn('next-week','›','icon-button','aria-label="Следующая неделя"')}</div></div><div class="week-grid">${days.map((name,i)=>{const day=shiftDay(first,i);return `<div class="week-column ${state.day===day?'selected':''}">${btn('day',`${name}<br>${Number(day.slice(8))}`,'day-label',`data-day="${day}" aria-label="${humanDate(day)}" aria-pressed="${state.day===day}"`)}${visible.filter(e=>localDay(e.scheduled_at)===day).map(e=>btn('day',`<small>${fmt(e.scheduled_at,{hour:'2-digit',minute:'2-digit'})}</small><span>${esc(e.title)}</span><b class="status-${e.status}">${e.status==='taken'?'✓':e.status==='skipped'?'!':'◷'}</b>`,'mini-event',`data-day="${day}"`)).join('')}</div>`;}).join('')}</div></section><aside class="day-panel"><h2>${humanDate(state.day)}</h2>${selected.length?selected.map(eventCard).join(''):'<p class="muted">На этот день событий нет.</p>'}<h3 class="spaced">Наблюдения</h3>${observations.filter(o=>localDay(o.created_at)===state.day).map(o=>`<p class="small">${fmt(o.created_at,{hour:'2-digit',minute:'2-digit'})} · ${esc(o.text)}</p>`).join('')||'<p class="small muted">Записей пока нет.</p>'}${btn('observation','Добавить наблюдение')}<h3 class="spaced">Прогресс курсов</h3>${courses.filter(c=>c.active&&(!state.courseFilter||c.id===state.courseFilter)).map(c=>`<div class="compact-course"><strong>${esc(c.title)}</strong>${courseProgress(c)}</div>`).join('')||'<p class="small muted">Курсы ещё не добавлены.</p>'}</aside></div>`;
}
function eventCard(e){
 const due=new Date(e.scheduled_at)<=new Date(Date.now()+300000);
 return `<article class="event"><div class="event-time">${fmt(e.scheduled_at,{hour:'2-digit',minute:'2-digit'})}</div><div class="event-body"><h3>${esc(e.title)}</h3><p>${esc(e.dosage||kinds[e.kind])}${e.notes?' · '+esc(e.notes):''}</p><span class="pill ${e.status}">${e.status==='taken'?'✓ Принято':e.status==='skipped'?'Пропущено':'◷ Ожидается'}</span><div class="event-actions">${due&&e.status==='pending'?btn('mark','✓ '+(['food','other'].includes(e.kind)?'Выполнено':'Принял'),'primary small',`data-id="${e.id}" data-status="taken"`)+btn('mark','Пропустить','small',`data-id="${e.id}" data-status="skipped"`)+btn('mark','Через 10 минут','text-button small',`data-id="${e.id}" data-status="snoozed"`):''}${e.status==='taken'||e.status==='skipped'?btn('mark','Снять отметку','text-button small',`data-id="${e.id}" data-status="pending"`):''}</div></div></article>`;
}
async function renderMedications(){
 [state.courses,state.plans]=await Promise.all([api('/courses'),api('/plans')]);
 $('#content').innerHTML=heading('Мои препараты','Активные назначения и препараты, добавленные вручную',btn('personal','＋ Добавить препарат','primary'))+tabs()+`<div class="course-grid">${state.courses.map(c=>`<article class="card course-card"><h2>${esc(c.title)}</h2><p>${esc(c.dosage||kinds[c.kind])} · ${c.times.length} приём(а) в день</p><p class="small muted">${humanDate(c.start_date)}${c.end_date?' — '+humanDate(c.end_date):' · без даты окончания'}</p><span class="pill ${c.source==='doctor'?'taken':'personal'}">${c.source==='doctor'?'Назначено врачом':'Добавлено мной'}</span>${courseProgress(c)}<div class="row">${btn('course-schedule','Открыть расписание','',`data-id="${c.id}"`)}${c.source==='personal'&&c.active?btn('stop-series','Остановить','text-button',`data-id="${c.id}"`):''}</div></article>`).join('')}</div>${!state.courses.length?empty('Курсов пока нет','Добавьте назначение врача или своё событие.',btn('nav-treatment','План лечения')):''}${state.plans.some(p=>!p.scheduled)?`<div class="banner"><div><h3>Есть назначения без расписания</h3><p>Добавьте их из плана лечения.</p></div>${btn('nav-treatment','Посмотреть назначения')}</div>`:''}<div class="help-banner spaced"><h3>Вопрос о сочетании препаратов?</h3><p>Сочетание препаратов можно обсудить с врачом на приёме. Автоматическая проверка совместимости пока не подключена.</p>${btn('booking','Записаться к врачу')}</div>`;
}
function planCard(p){return `<article class="card plan-card"><div class="row space"><h2>${esc(p.title)}</h2><span class="pill ${p.scheduled?'taken':''}">${p.scheduled?'В календаре':'Новое назначение'}</span></div><p class="small muted">${esc(p.doctor_name)} · ${fmt(p.created_at,{day:'numeric',month:'long'})}</p><p class="small">${esc(p.notes)}</p>${p.items.map(i=>`<div class="plan-item"><h3>${esc(i.name)} · ${esc(i.strength)}</h3><p>${esc(i.dosage)} · ${esc(i.times.join(', '))}</p><small>${humanDate(i.start_date)} — ${humanDate(i.end_date)}</small></div>`).join('')}<div class="row spaced">${btn('cart','Купить в ЕАПТЕКЕ ↗','primary',`data-id="${p.cart_id}"`)}${!p.scheduled?btn('activate','Уже есть · добавить курс','',`data-id="${p.id}" data-source="already_have"`):btn('nav-medications','Прогресс курсов')}</div></article>`;}
async function renderTreatment(){state.plans=await api('/plans');$('#content').innerHTML=heading('План лечения','Назначения вашего врача из внешней клиники')+tabs()+state.plans.map(planCard).join('')+(state.config.demo?demoBanner():'');}
async function showPlans(){state.view='treatment';await render();}
async function showCart(id){
 const cart=await api('/carts/'+id);state.cart=cart;
 openModal('Препараты по назначению',`<p class="muted">Список для покупки в ЕАПТЕКЕ</p>${cart.lines.map(i=>`<div class="plan-item"><b>${esc(i.name)} · ${esc(i.strength)}</b><p>${esc(i.form)} · ${esc(i.dosage)}</p></div>`).join('')}<p class="small muted">Заказ и доставку оформите на сайте ЕАПТЕКИ. Список не переносится автоматически; наличие и стоимость уточняются там.</p><div class="row"><a class="button primary" href="https://www.eapteka.ru/" target="_blank" rel="noopener noreferrer">Перейти в ЕАПТЕКУ ↗</a>${btn('copy-cart','Скопировать список')}</div><hr><p>Уже приобрели препараты?</p>${cart.status==='confirmed'?btn('activate','Добавить курс в календарь','primary',`data-id="${cart.plan_id}" data-source="purchase"`):btn('purchase','Подтвердить покупку','',`data-id="${cart.id}"`)}<p class="small muted">Подтверждение с ваших слов. Мы не получаем сведения об оплате из ЕАПТЕКИ.</p>`);
}
function personalForm(){openModal('Добавить препарат или событие',`<form id="personal-form"><div class="form-grid"><div class="full"><label>Название</label><input name="title" placeholder="Препарат, витамин или завтрак" maxlength="160" required></div><div><label>Категория</label><select name="kind">${Object.entries(kinds).map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select></div><div><label>Дозировка (если применимо)</label><input name="dosage" maxlength="160" placeholder="Как указано в вашем назначении"></div>${scheduleFields()}<div class="full"><label>Примечание</label><input name="notes" maxlength="2000" placeholder="Например, после еды"></div></div><p class="small muted">Будет отмечено как добавленное самостоятельно. Для процента завершения укажите дату окончания.</p><button class="primary" type="submit">Добавить в календарь</button></form>`);}
function expiryLabel(d){if(d.days_left===null)return 'Срок не указан';if(d.days_left<0)return 'Срок истёк';if(d.days_left===0)return 'Действует до конца дня';return `Осталось ${d.days_left} дн.`;}
async function renderDocuments(){
 state.documents=await api('/documents');
 $('#content').innerHTML=heading('Документы','Медицинская библиотека и история',btn('new-document','＋ Добавить документ','primary'))+`<div class="documents-layout"><section><input type="search" id="document-search" aria-label="Найти документ" placeholder="⌕  Найти документ" value="${esc(state.docSearch)}"><div class="tabs spaced">${Object.entries(documentKinds).map(([v,n])=>btn('document-filter',n,state.docFilter===v?'selected':'',`data-filter="${v}"`)).join('')}</div><div id="document-list"></div><p class="small muted spaced">Срок задаётся по документу или требованиям принимающей организации. Если он неизвестен — укажите дату самостоятельно.</p></section><aside class="day-panel" id="document-preview"></aside></div>`;renderDocumentList();renderDocumentPreview();
}
function renderDocumentList(){
 const list=state.documents.filter(d=>(state.docFilter==='all'||d.kind===state.docFilter)&&d.title.toLowerCase().includes(state.docSearch.toLowerCase()));
 $('#document-list').innerHTML=list.map(d=>`<button class="document-card ${state.selectedDocument===d.id?'active':''}" data-action="document-select" data-id="${d.id}"><span><strong>${esc(d.title)}</strong><small>${humanDate(d.issued_on)} · ${esc(d.issuer||'Добавлено вами')}</small><span class="expiry ${d.validity}">◷ ${expiryLabel(d)}${d.expires_on?' · до '+humanDate(d.expires_on):''}</span></span><span class="pill ${d.validity==='expired'?'skipped':'taken'}">${esc(documentKinds[d.kind])}</span></button>`).join('')||empty('Документов не найдено','Добавьте документ или измените фильтр.');
 if(!list.some(d=>d.id===state.selectedDocument))state.selectedDocument=list[0]?.id||null;
 renderDocumentPreview();
}
function renderDocumentPreview(){const d=state.documents.find(d=>d.id===state.selectedDocument);$('#document-preview').innerHTML=`<h2>Предпросмотр</h2>${d?`<h3>${esc(d.title)}</h3><p class="small muted">${humanDate(d.issued_on)} · ${esc(d.issuer)}</p><div class="document-paper pre">${esc(d.content||'Содержимое документа не добавлено.')}</div><p class="expiry ${d.validity}">${expiryLabel(d)}</p>${btn('document-open','Открыть документ','primary',`data-id="${d.id}"`)}<div class="spaced">${btn('document-edit','Изменить срок и данные','text-button',`data-id="${d.id}"`)}</div>`:'<p class="muted">Выберите документ из списка.</p>'}`;}
function documentForm(id){const d=state.documents.find(d=>d.id===Number(id))||{};openModal(d.id?'Изменить документ':'Добавить документ',`<form id="document-form" data-id="${d.id||''}"><div class="form-grid"><div class="full"><label>Название</label><input name="title" required maxlength="160" value="${esc(d.title)}"></div><div><label>Тип</label><select name="kind">${Object.entries(documentKinds).filter(([k])=>k!=='all').map(([k,v])=>`<option value="${k}" ${d.kind===k?'selected':''}>${v}</option>`).join('')}</select></div><div><label>Выдан организацией</label><input name="issuer" maxlength="160" value="${esc(d.issuer)}"></div><div><label>Дата документа</label><input type="date" name="issued_on" required value="${d.issued_on||localDay()}"></div><div><label>Действует до (необязательно)</label><input type="date" name="expires_on" value="${d.expires_on||''}"></div><div class="full"><label>Текст документа</label><textarea name="content" maxlength="12000">${esc(d.content)}</textarea></div></div><p class="small muted">Указывайте срок из документа или требования организации.</p><button type="submit" class="primary">Сохранить документ</button></form>`);}
async function renderAppointments(){const bookings=await api('/booking');$('#content').innerHTML=heading('Записи','Ваши приёмы у специалистов',btn('booking','＋ Записаться к врачу','primary'))+`<div class="course-grid">${bookings.map(b=>`<article class="card"><h2>${esc(b.specialty)}</h2><h3>${esc(b.doctor)}</h3><p>${esc(b.starts_at.slice(0,16).replace('T',' '))}</p><span class="pill">${b.status==='booked'?'Запланировано':'Отменено'}</span>${b.status==='booked'?btn('cancel-booking','Отменить запись','text-button',`data-id="${b.id}"`):''}</article>`).join('')}</div>${!bookings.length?empty('Записей пока нет','Выберите специалиста и удобное время.',btn('booking','Выбрать время')):''}<p class="small muted spaced">Для демонстрации используется тестовое расписание клиники.</p>`;}
async function renderHistory(){const [observations,courses]=await Promise.all([api('/observations'),api('/courses')]);$('#content').innerHTML=heading('История здоровья','Наблюдения и результаты прохождения курсов',btn('observation','＋ Добавить наблюдение','primary'))+`<div class="home-grid"><section><h2>Дневник самочувствия</h2>${observations.map(o=>`<article class="card spaced"><p class="small muted">${fmt(o.created_at,{day:'numeric',month:'long',hour:'2-digit',minute:'2-digit'})}</p><p class="pre">${esc(o.text)}</p></article>`).join('')||empty('Дневник пока пуст','Сохраняйте наблюдения о самочувствии.')}</section><aside><h2>Курсы</h2>${courses.map(c=>`<article class="card spaced"><h3>${esc(c.title)}</h3>${courseProgress(c)}</article>`).join('')||'<p class="muted">Курсов пока нет.</p>'}</aside></div>`;}

const assistantMark=`<svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M16 2c1.5 9 5 12.5 14 14-9 1.5-12.5 5-14 14C14.5 21 11 17.5 2 16 11 14.5 14.5 11 16 2Z" fill="currentColor"/></svg>`;
function chatTurn(message,pending=false){
 const user=message.role==='user';
 return `<article class="chat-turn ${user?'from-user':'from-assistant'}" ${pending?'data-pending-chat':''}><div class="turn-author">${user?'Вы':assistantMark+'Помощник'}</div><div class="bubble ${user?'user':''}">${esc(message.content)}</div>${!user?chatSuggestion(message.action):''}</article>`;
}
function chatWaiting(){return `<div class="chat-waiting" data-pending-chat role="status"><span class="thinking-dots" aria-hidden="true"><i></i><i></i><i></i></span>Модель готовит ответ…</div>`;}
function fitChatInput(){const input=$('#chat-form textarea');if(input){input.style.height='auto';input.style.height=Math.min(input.scrollHeight,180)+'px';}}
function syncChatEmpty(){const chat=$('.chat-workspace');if(chat)chat.classList.toggle('is-empty',!$('#messages').children.length);}
async function renderChat(){
 const accountId=state.me.id;
 const history=await api('/chat/history');
 if(state.me?.id!==accountId || state.view!=='chat')return;
 const pending=state.chatPending?.accountId===accountId ? state.chatPending : null;
 const starters=[['Записаться к врачу','Хочу записаться к терапевту. Как выбрать время?'],['Открыть назначения','Где посмотреть мои назначения?'],['Настроить календарь','Как добавить свои витамины в календарь?']];
 $('#content').innerHTML=`<section class="chat-workspace ${!history.length&&!pending?'is-empty':''}" aria-label="Чат с помощником"><header class="chat-header"><div class="chat-header-name"><span class="chat-mini-mark">${assistantMark}</span><span>Медицинский помощник</span></div><span class="chat-private">Ваш личный диалог</span></header><div class="chat-stage"><div class="chat-hero"><span class="chat-hero-mark"><img src="/static/images/assistant-orb.jpg" width="144" height="144" alt="" aria-hidden="true"></span><p class="chat-eyebrow">Чат с помощником</p><h1>Запись к врачу,<br><span>лекарства и календарь</span></h1><p class="chat-intro">Здесь можно узнать, как записаться к врачу,<br class="desktop-break"> найти назначения и добавить напоминания о лекарствах.</p></div><div class="messages" id="messages" role="log" aria-label="История переписки" aria-live="polite" aria-relevant="additions">${history.map(m=>chatTurn(m)).join('')}${pending?chatTurn({role:'user',content:pending.message},true)+chatWaiting():''}</div><div class="chat-input-area"><form id="chat-form" class="composer"><textarea name="message" aria-label="Сообщение помощнику" placeholder="Напишите вопрос здесь…" rows="2" maxlength="4000" required ${pending?'disabled':''}></textarea><div class="composer-toolbar"><span class="composer-context">${assistantMark} Напишите вопрос и нажмите стрелку</span><button class="chat-send" type="submit" aria-label="Отправить сообщение" title="Отправить сообщение" ${pending?'disabled':''}><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div></form><div class="chat-starters">${starters.map(([label,prompt])=>btn('chat-starter',`${esc(label)} <span aria-hidden="true">↗</span>`,'',`data-prompt="${esc(prompt)}" ${pending?'disabled':''}`)).join('')}</div>${!state.config.llm.configured?'<p class="chat-availability">Помощник всегда на связи. Вы можете <button type="button" data-action="booking">выбрать врача</button> или <button type="button" data-action="plans">открыть назначения</button>.</p>':''}<p class="chat-footnote">Помощник помогает пользоваться сервисом и не заменяет консультацию врача.</p></div></div></section>`;
 $('#messages').scrollTop=$('#messages').scrollHeight;
}
function scheduleFields(prefix=''){
 return `<div><label>Дата начала</label><input name="start_date" type="date" required value="${localDay()}"></div><div><label>Дата окончания</label><input name="end_date" type="date"></div><div><label>Время через запятую</label><input name="times" placeholder="09:00, 21:00" required></div><div><label>Часовой пояс</label><input name="timezone" value="${esc(state.me.timezone)}" required></div><div class="full"><label>Дни недели</label><div class="check-row">${days.map((d,i)=>`<label><input type="checkbox" name="weekdays" value="${i}" checked>${d}</label>`).join('')}</div></div>`;
}
async function showBooking(){
 const [slots,bookings]=await Promise.all([api('/booking/slots'),api('/booking')]);
 state.slots=slots;
 openModal('Запись к врачу',`${bookings.filter(b=>b.status==='booked').length?`<h3>Ваши записи</h3>${bookings.filter(b=>b.status==='booked').map(b=>`<div class="booking-option"><div><h3>${esc(b.doctor)}</h3><p>${esc(b.specialty)} · ${esc(b.starts_at.slice(0,16).replace('T',' '))}</p></div>${btn('cancel-booking','Отменить запись','text-button danger',`data-id="${b.id}"`)}</div>`).join('')}`:''}<h3 style="margin-top:25px">Свободное время</h3><label>Специальность</label><select id="specialty-filter"><option value="">Все специалисты</option>${[...new Set(slots.map(s=>s.specialty))].map(s=>`<option>${esc(s)}</option>`).join('')}</select><div id="booking-list" class="booking-list"></div><p class="small muted">Тестовые врачи и расписание. Время приёма — местное время клиники.</p>`);
 renderSlots();
}
function renderSlots(){const specialty=$('#specialty-filter').value;$('#booking-list').innerHTML=state.slots.filter(s=>!specialty||s.specialty===specialty).map(s=>`<div class="booking-option"><div><h3>${esc(s.doctor)}</h3><p>${esc(s.specialty)} · ${esc(s.starts_at.slice(0,16).replace('T',' '))}</p></div>${btn('book-confirm','Выбрать','',`data-id="${s.id}"`)}</div>`).join('')||'<p class="muted small">Свободных окон пока нет.</p>';}
function scheduleFrom(container){const v=name=>container.querySelector(`[name="${name}"]`).value;return {start_date:v('start_date'),end_date:v('end_date')||null,times:v('times').split(',').map(s=>s.trim()).filter(Boolean),weekdays:[...container.querySelectorAll('[name=weekdays]:checked')].map(el=>Number(el.value)),timezone:v('timezone')};}
async function enableNotifications(){
 if(!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window))throw new Error('Этот браузер не поддерживает фоновые напоминания. Календарь и напоминания в открытом приложении доступны.');
 const permission=await Notification.requestPermission();if(permission!=='granted')throw new Error('Разрешите уведомления в настройках браузера, чтобы получать напоминания.');
 const registration=await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;
 const {public_key}=await api('/push/key');const raw=atob(public_key.replace(/-/g,'+').replace(/_/g,'/'));const key=Uint8Array.from(raw,c=>c.charCodeAt(0));
 let subscription=await registration.pushManager.getSubscription();if(!subscription)subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
 await api('/push/subscribe','POST',subscription.toJSON());toast('Фоновые напоминания включены для этого браузера.');
}
async function unsubscribeDevice(){
 if(!('serviceWorker' in navigator))return;
 const registration=await navigator.serviceWorker.getRegistration();if(!registration)return;
 const subscription=await registration.pushManager.getSubscription();if(!subscription)return;
 await api('/push/unsubscribe','POST',{endpoint:subscription.endpoint});await subscription.unsubscribe();
}
let polling=false;
async function pollReminders(){
 if(polling||state.me?.role!=='patient')return;polling=true;
 try{const reminders=await api('/reminders');const container=$('#reminders');if(container)container.innerHTML=reminders.map(r=>`<div class="reminder-banner"><b>Пора: ${esc(r.event.title)}</b><p>${esc(r.event.dosage||kinds[r.event.kind])} · ${fmt(r.event.scheduled_at,{hour:'2-digit',minute:'2-digit'})}</p><div class="row">${btn('mark','✓ Выполнено','primary small',`data-id="${r.event.id}" data-status="taken"`)}${btn('mark','Через 10 минут','small',`data-id="${r.event.id}" data-status="snoozed"`)}${btn('ack','Скрыть','text-button small',`data-id="${r.id}"`)}</div></div>`).join('');}catch{/* session may expire; keep user form intact */}finally{polling=false;}
}
async function sendChat(form){
 const message=form.elements.message.value.trim();
 if(!message || state.chatPending)return;
 const accountId=state.me.id;
 state.chatPending={accountId,message};
 form.elements.message.disabled=true;
 $('#messages').insertAdjacentHTML('beforeend',chatTurn({role:'user',content:message},true)+chatWaiting());
 syncChatEmpty();
 $('#messages').scrollTop=$('#messages').scrollHeight;
 let succeeded=false;
 try{
  await api('/chat','POST',{message});
  succeeded=true;
  state.chatPending=null;
  if(state.me?.id!==accountId || state.view!=='chat')return;
  await renderChat();
  if(state.me?.id!==accountId || state.view!=='chat')return;

 }finally{
  state.chatPending=null;
  if(state.me?.id===accountId && state.view==='chat'){
   document.querySelectorAll('[data-pending-chat]').forEach(el=>el.remove());
   const current=$('#chat-form');
   if(current){current.elements.message.disabled=false;current.elements.message.value=succeeded?'':message;current.querySelector('button[type=submit]').disabled=false;fitChatInput();}
   syncChatEmpty();
  }
 }
}
async function action(button){
 const a=button.dataset.action,id=button.dataset.id;
 if(a==='chat-starter'){const input=$('#chat-form textarea');if(input&&!input.disabled){input.value=button.dataset.prompt;fitChatInput();input.focus();}return;}
 if(a==='close'){closeModal();return;}
 if(a==='login-patient'){await api('/demo/login','POST',{role:'patient'});await load();return;}
 if(a==='logout'){await unsubscribeDevice();await api('/logout','POST',{});state.day=null;closeModal();await load();return;}
 if(a.startsWith('nav-')){state.view=a.slice(4);state.courseFilter=null;await render();return;}
 if(a==='profile'){openModal('Профиль и настройки',`<h3>${esc(state.me.name)}</h3><p class="muted">Часовой пояс: ${esc(state.me.timezone)}</p><div class="row">${btn('notifications','Включить напоминания','primary')}${btn('logout','Выйти')}</div>`);return;}
 if(a==='personal'){personalForm();return;}
 if(a==='plans'){await showPlans();return;}
 if(a==='cart'){await showCart(id);return;}
 if(a==='copy-cart'){await navigator.clipboard.writeText(state.cart.lines.map(i=>`${i.name} · ${i.strength} · ${i.form}`).join('\n'));toast('Список скопирован');return;}
 if(a==='purchase'){await api(`/carts/${id}/confirm`,'POST',{});await showCart(id);toast('Покупка подтверждена вами. Можно добавить расписание.');return;}
 if(a==='simulate'){await api('/demo/prescription','POST',{});state.view='treatment';await render();toast('Демоназначение и документы доступны');return;}
 if(a==='booking'){await showBooking();return;}
 if(a==='day'||a==='today'||a.endsWith('-week')){state.day=a==='day'?button.dataset.day:a==='today'?localDay():shiftDay(state.day,a==='previous-week'?-7:7);await renderCalendar();return;}
 if(a==='course-schedule'){state.courseFilter=Number(id);const c=state.courses.find(c=>c.id===Number(id));state.day=c.start_date>localDay()?c.start_date:c.end_date&&c.end_date<localDay()?c.end_date:localDay();state.view='calendar';await render();return;}
 if(a==='clear-course'){state.courseFilter=null;await renderCalendar();return;}
 if(a==='activate'){await api(`/plans/${id}/calendar`,'POST',{source:button.dataset.source});closeModal();state.view='medications';await render();toast('Весь курс добавлен в календарь');return;}
 if(a==='mark'){await api(`/calendar/events/${id}/status`,'POST',{status:button.dataset.status});await render();await pollReminders();toast(button.dataset.status==='snoozed'?'Напомним через 10 минут':'Отметка сохранена');return;}
 if(a==='ack'){await api(`/reminders/${id}/ack`,'POST',{});await pollReminders();return;}
 if(a==='stop-series'){openModal('Остановить личное событие?',`<p>Будущие события серии будут отменены. История выполненного сохранится.</p><div class="modal-footer">${btn('close','Оставить')}${btn('stop-confirm','Остановить','primary',`data-id="${id}"`)}</div>`);return;}
 if(a==='stop-confirm'){await api(`/calendar/series/${id}`,'DELETE');closeModal();await render();toast('Повторение остановлено');return;}
 if(a==='book-confirm'){const slot=state.slots.find(s=>s.id===Number(id));openModal('Подтвердите запись',`<h3>${esc(slot.doctor)}</h3><p>${esc(slot.specialty)} · ${esc(slot.starts_at.slice(0,16).replace('T',' '))}</p><p class="small muted">Будет создана запись в тестовом расписании.</p><div class="modal-footer">${btn('booking','Назад')}${btn('book','Подтвердить запись','primary',`data-id="${id}"`)}</div>`);return;}
 if(a==='book'){await api('/booking','POST',{slot_id:Number(id)});await showBooking();toast('Запись на приём создана');return;}
 if(a==='cancel-booking'){openModal('Отменить запись?',`<p>Выбранное время снова станет доступно для записи.</p><div class="modal-footer">${btn('close','Оставить запись')}${btn('cancel-booking-confirm','Отменить запись','danger',`data-id="${id}"`)}</div>`);return;}
 if(a==='cancel-booking-confirm'){await api('/booking/'+id,'DELETE');closeModal();await render();toast('Запись отменена');return;}
 if(a==='notifications'){await enableNotifications();return;}
 if(a==='observation'){openModal('Как вы себя чувствуете?',`<form id="observation-form"><label>Наблюдение за сегодня</label><textarea name="text" required maxlength="2000" placeholder="Что вы заметили?"></textarea><p class="small muted">Запись сохранится в вашем дневнике. Автоматическая передача во внешнюю клинику не подключена.</p><button class="primary" type="submit">Сохранить наблюдение</button></form>`);return;}
 if(a==='document-filter'){state.docFilter=button.dataset.filter;await renderDocuments();return;}
 if(a==='document-select'){state.selectedDocument=Number(id);renderDocumentList();return;}
 if(a==='new-document'||a==='document-edit'){documentForm(id);return;}
 if(a==='document-open'){const d=state.documents.find(d=>d.id===Number(id));openModal(d.title,`<p class="small muted">${humanDate(d.issued_on)} · ${esc(d.issuer)}</p><div class="document-paper pre">${esc(d.content||'Содержимое не добавлено.')}</div><div class="row spaced">${btn('document-download','Скачать текст','primary',`data-id="${d.id}"`)}${btn('document-edit','Изменить','',`data-id="${d.id}"`)}</div>`);return;}
 if(a==='document-download'){const d=state.documents.find(d=>d.id===Number(id));const blob=new Blob([`${d.title}\n${d.issued_on}\n${d.issuer}\n\n${d.content}`],{type:'text/plain;charset=utf-8'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download='document-'+d.id+'.txt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return;}
}
document.addEventListener('click',async e=>{const button=e.target.closest('button[data-action]');if(!button||button.disabled)return;button.disabled=true;try{await action(button);}catch(err){toast(err.message,true);}finally{button.disabled=false;}});
document.addEventListener('change',e=>{if(e.target.id==='specialty-filter')renderSlots();});
document.addEventListener('input',e=>{if(e.target.id==='document-search'){state.docSearch=e.target.value;renderDocumentList();}});
document.addEventListener('submit',async e=>{
 const form=e.target;e.preventDefault();const submit=form.querySelector('button[type=submit]');if(!submit||submit.disabled)return;submit.disabled=true;
 try{
  if(form.id==='personal-form'){const body={...scheduleFrom(form),title:form.elements.title.value,kind:form.elements.kind.value,dosage:form.elements.dosage.value,notes:form.elements.notes.value};await api('/calendar/series','POST',body);state.day=body.start_date;state.view='medications';closeModal();await render();toast('Событие добавлено в календарь');}
  else if(form.id==='document-form'){const data=Object.fromEntries(new FormData(form));data.expires_on=data.expires_on||null;await api('/documents'+(form.dataset.id?'/'+form.dataset.id:''),form.dataset.id?'PUT':'POST',data);closeModal();await renderDocuments();toast('Документ сохранён');}
  else if(form.id==='observation-form'){await api('/observations','POST',{text:form.elements.text.value});closeModal();await render();toast('Наблюдение сохранено');}
  else if(form.id==='chat-form'){await sendChat(form);}
 }catch(err){toast(err.message,true);}finally{submit.disabled=false;}
});
$('#modal').addEventListener('click',e=>{if(e.target===$('#modal')){const r=$('#modal').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeModal();}});
let fieldId=0;
new MutationObserver(()=>{document.querySelectorAll('label:not([for])').forEach(label=>{const input=label.nextElementSibling;if(input&&['INPUT','TEXTAREA','SELECT'].includes(input.tagName)){if(!input.id)input.id='field-'+(++fieldId);label.htmlFor=input.id;}});}).observe(document.body,{childList:true,subtree:true});
load().catch(err=>{$('#app').innerHTML=empty('Не удалось загрузить приложение',err.message);});

let documentDay=localDay();
setInterval(()=>{const day=localDay();if(day!==documentDay){documentDay=day;if(state.me&&state.view==='documents')renderDocuments().catch(err=>toast(err.message,true));}},60000);

document.addEventListener('input',e=>{if(e.target.matches('#chat-form textarea'))fitChatInput();});
document.addEventListener('keydown',e=>{if(e.target.matches('#chat-form textarea')&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&window.matchMedia('(pointer:fine)').matches){e.preventDefault();if(!state.chatPending)e.target.form.requestSubmit();}});
