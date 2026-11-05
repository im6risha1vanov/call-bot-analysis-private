/* Display helpers only. Never used for storage, due-date calculations or API payloads. */
(function(root){
  'use strict';
  var months=['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
  function date(value,options){
    options=options||{};var match=/^(\d{4})-(\d{2})-(\d{2})(?:$|T| )/.exec(String(value||''));
    if(!match)return value?'Дата не указана':'—';
    var y=+match[1],m=+match[2],d=+match[3],dt=new Date(Date.UTC(y,m-1,d));
    if(dt.getUTCFullYear()!==y||dt.getUTCMonth()!==m-1||dt.getUTCDate()!==d)return 'Дата не указана';
    if(options.today&&options.relative){
      var today=new Date(options.today+'T12:00:00Z'),diff=Math.round((dt-new Date(options.today+'T00:00:00Z'))/864e5);
      if(!isNaN(today.getTime())&&diff===0)return 'сегодня';if(!isNaN(today.getTime())&&diff===1)return 'завтра';
    }
    return d+' '+months[m-1]+(options.year?' '+y:'');
  }
  function zone(value){
    var names={'Europe/Moscow':'по Москве','Europe/Kaliningrad':'по Калининграду','Europe/Samara':'по Самаре','Asia/Yekaterinburg':'по Екатеринбургу','Asia/Novosibirsk':'по Новосибирску','Asia/Irkutsk':'по Иркутску','Asia/Vladivostok':'по Владивостоку','UTC':'по всемирному времени'};
    return names[value]||'по времени компании';
  }
  function datesInText(value){return String(value==null?'':value).replace(/\b\d{4}-\d{2}-\d{2}\b/g,function(v){return date(v);});}
  function label(value,labels,fallback){
    if(labels&&Object.prototype.hasOwnProperty.call(labels,value))return labels[value];
    var text=String(value||'');return /^[А-Яа-яЁё]/.test(text)&&!/[{}<>]|Traceback|Error:/.test(text)?text:(fallback||'Не указано');
  }
  function error(value,fallback){
    var raw=String(value&&value.message||value||'').trim();
    // API errors are untrusted: a stack trace may contain Russian record values.
    if(!raw||raw.length>500||/[{}<>]|(?:Traceback|Exception|Error:|SQLSTATE|SELECT\s|INSERT\s|UPDATE\s|DELETE\s|postgres|asyncpg|node:|\/opt\/|\.py:\d|https?:\/\/|Europe\/|UTC[+-]|\b[a-z]+_[a-z]+\b|\b[A-Z][A-Z_]{3,}\b)/i.test(raw)||!/[А-Яа-яЁё]/.test(raw))return fallback||'Не удалось выполнить действие. Попробуйте ещё раз.';
    var readable=datesInText(raw);return /попроб|проверь|укаж|выбер|открой|вернит|обратит|войд|нужен|нужна|добавьте|сверьте|дождитесь|повторите/i.test(readable)?readable:readable+' '+(fallback||'Попробуйте ещё раз.');
  }
  function operation(path,method){
    if((method||'GET')==='GET')return 'Не удалось загрузить данные. Обновите вкладку или попробуйте позже.';
    if(/\/outcome(?:\?|$)/.test(path))return 'Не удалось сохранить итог звонка. Проверьте поля и попробуйте ещё раз.';
    if(/\/tasks\//.test(path))return 'Не удалось сохранить перезвон. Попробуйте ещё раз.';
    if(/\/dial(?:\?|$)/.test(path))return 'Не удалось начать звонок. Проверьте подключение телефона и попробуйте ещё раз.';
    if(/\/contracts\//.test(path))return 'Не удалось подготовить договор. Проверьте реквизиты и попробуйте ещё раз.';
    return 'Не удалось сохранить изменения. Проверьте поля и попробуйте ещё раз.';
  }
  var api={date:date,zone:zone,datesInText:datesInText,label:label,error:error,operation:operation};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.CRMText=api;
}(this));
