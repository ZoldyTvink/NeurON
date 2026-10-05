'use strict';
self.addEventListener('push',event=>{
  let data;try{data=event.data.json();}catch{return;}
  event.waitUntil(self.registration.showNotification(data.title || 'Meditron',{
    body:data.body || 'Откройте календарь',tag:data.tag || 'meditron',
    data:{url:'/',event_id:data.event_id},requireInteraction:true
  }));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  event.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(windows=>{
    const client=windows.find(w=>new URL(w.url).origin===self.location.origin);
    if(client)return client.focus();return self.clients.openWindow('/');
  }));
