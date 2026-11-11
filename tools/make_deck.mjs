import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const rt=process.env.HOTEL_RUNTIME;
const skill=process.env.HOTEL_SLIDES_SKILL;
if(!rt||!skill)throw new Error('Set HOTEL_RUNTIME and HOTEL_SLIDES_SKILL.');
const moduleDir=path.join(rt,'node','node_modules');
process.env.CODEX_NODE_MODULES_ROOT=moduleDir;
process.env.NODE_PATH=moduleDir;
process.env.RUNTIME_NODE_MODULES=moduleDir;
const {Presentation,PresentationFile}=await import(pathToFileURL(path.join(moduleDir,'@oai','artifact-tool','dist','artifact_tool.mjs')).href);
const {finalizePresentation}=await import(pathToFileURL(path.join(skill,'container_tools','artifact_tool_utils.mjs')).href);
const qa=path.join(root,'build','slides-qa');
await fs.mkdir(qa,{recursive:true});
await fs.mkdir(path.join(root,'deliverables'),{recursive:true});
const data=JSON.parse(await fs.readFile(path.join(root,'build','results.json'),'utf8'));
const f=(v,n=2)=>new Intl.NumberFormat('ru-RU',{minimumFractionDigits:n,maximumFractionDigits:n}).format(v);
const occ=data.targets.occupancy, adr=data.targets.adr;
const peak=data.forecast.center.reduce((a,b)=>a.occupancy.value>b.occupancy.value?a:b);
const P=Presentation.create({slideSize:{width:1280,height:720}});
const C={ink:'#15313B', muted:'#4A626A', paper:'#F5F7F2', white:'#FFFFFF', accent:'#08766C', dark:'#15343B', light:'#BEEBBE', line:'#D7E7DD'};
const FONT='Arial';
function text(s,str,x,y,w,h,{size=30,color=C.ink,bold=false,align='left'}={}){
  const sh=s.shapes.add({name:str.slice(0,60),geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',
    line:{fill:'none',width:0}});
  sh.text=str;
  sh.text.style={fontFamily:FONT,fontSize:size,bold,color,alignment:align,verticalAlignment:'middle',
    insets:{left:0,top:0,right:0,bottom:0}};
  return sh;
}
function slide(k,title,notes,dark=false){
  const s=P.slides.add();
  s.background.fill=dark?C.dark:C.paper;
  const main=dark?C.white:C.ink, accent=dark?C.light:C.accent;
  text(s,title,76,58,1120,75,{size:47,color:main,bold:true});
  text(s,'ГОРИЗОНТ   /   ТЕМА 17',76,654,950,25,{size:15,color:accent,bold:true});
  text(s,String(k).padStart(2,'0'),1138,654,58,25,{size:17,color:accent,bold:true,align:'right'});
  s.speakerNotes.text=notes;
  return s;
}
function item(s,name,body,x,y,w=522){
  text(s,name,x,y,w,43,{size:28,bold:true,color:C.accent});
  text(s,body,x,y+49,w,94,{size:25});
}
function labelled(s,n,title,body,y){
  text(s,n,76,y,60,52,{size:25,bold:true,color:C.accent});
  text(s,title,146,y,350,52,{size:28,bold:true});
  text(s,body,516,y,677,65,{size:24,color:C.muted});
}
function nativeTable(s,values,y,columns){
  const width=columns.reduce((a,b)=>a+b,0);
  const t=s.tables.add({rows:values.length,columns:columns.length,left:76,top:y,width,height:values.length*59,
    columnWidths:columns,values});
  t.styleOptions={headerRow:false,bandedRows:false,firstColumn:false};
  for(let i=0;i<values.length;i++){
    t.rows[i].height=59;
    for(let j=0;j<columns.length;j++){
      const c=t.getCell(i,j);
      c.fill=i===0?C.dark:i===2?'#E0F0E4':i%2?'#FFFFFF':'#F0F5EF';
      c.text.style={fontFamily:FONT,fontSize:i===0?20:24,bold:i===0||i===2,color:i===0?C.white:C.ink};
    }
  }
  t.borders.assign({style:'solid',fill:C.line,width:1});
}
{
  const s=P.slides.add();
  s.background.fill=C.dark;
  text(s,'НИТУ МИСИС   /   ИНТЕЛЛЕКТУАЛЬНЫЙ АНАЛИЗ ДАННЫХ',76,92,1110,42,{size:19,color:C.light,bold:true});
  text(s,'Прогноз загрузки и цены\nгостиничных номеров',76,199,1130,200,{size:70,color:C.white,bold:true});
  text(s,'Семестровый проект по теме 17 «Гостиничный бизнес»',78,457,1120,64,{size:28,color:'#DBEDE5'});
  text(s,'2026',78,624,1110,32,{size:20,color:C.light,bold:true});
  s.speakerNotes.text='0:00–0:20. Представьтесь. Тема 17 из методических указаний НИТУ МИСИС: загрузка номерного фонда и средняя цена. Покажу постановку, временную проверку и работающую демонстрацию.';
}
{
  const s=slide(2,'Два прогноза на 14 дней','0:20–0:55. Показатели рассчитываются на гостиницу и дату. Загрузка использует доступный фонд, ADR относится к реально проданным номерам. Оценки помогают выбрать даты для внимания. Оптимизация тарифа не входит в задачу.');
  text(s,'3',76,171,160,130,{size:103,bold:true,color:C.accent});
  text(s,'учебные гостиницы',76,304,470,54,{size:31,bold:true});
  text(s,'Центр, Экспо и Парк',76,367,470,53,{size:26,color:C.muted});
  item(s,'Загрузка номерного фонда','Проданные номеро-ночи / доступные.\nПомогает планировать смены.',610,170,585);
  item(s,'Средняя цена номера · ADR','Выручка / проданные номеро-ночи.\nПомогает проверить план тарифов.',610,372,585);
}
{
  const s=slide(3,'Данные и архитектура','0:55–1:35. Два источника работают офлайн и имеют разные форматы. CSV содержит искусственные пропуски и аномалии. HTML даёт текстовые, временные и пространственные признаки. Публичный режим KudaGo экспериментальный: живой запрос в проверочной среде ответил HTTP 403. Документация: https://docs.kudago.com/api/.');
  labelled(s,'01','Источники', 'Суточный CSV гостиниц и HTML-анонсы.\nKudaGo JSON доступен отдельным режимом.',159);
  labelled(s,'02','Хранение и контроль','SQLite сохраняет сырой снимок и хеш,\nнормализованные факты и журнал.',267);
  labelled(s,'03','Расчёт', 'Признаки на дату выпуска, подбор модели,\nотложенная оценка и прогноз.',375);
  labelled(s,'04','Интерфейс', 'Интерактивный дашборд с пояснениями\nв браузере, выгрузка в CSV.',483);
}
{
  const s=slide(4,'Что модель знает на дату выпуска','1:35–2:10. Лаги и окна рассчитываются от даты выпуска, не от будущего дня. Наблюдения после выпуска не меняют вектор признаков; это проверено тестом. Координаты используются как экспоненциальная функция расстояния. У анонсов учитывается дата публикации.');
  item(s,'История гостиницы','Последний факт, лаги 7, 14 и 28 дней,\nскользящие средние и день недели.',76,169);
  item(s,'Календарь','День недели, годовой цикл, номер\nдня горизонта и гостиница.',650,169,545);
  item(s,'Афиша','Дата, категория из текста и близость\nплощадки. Только известные анонсы.',76,374,555);
  item(s,'Защита от утечки','Одна функция признаков для теста\nи прогноза. Будущие факты закрыты.',650,374,545);
}
{
  const s=slide(5,'Временное разбиение','2:10–2:50. Валидация и тест содержат по восемь выпусков. В каждом окне оцениваются 14 следующих дней. Настройки подбираются только по валидации. Для теста модели обучены заново на периоде до 22 июля и остаются фиксированными. Три класса: сезонный ориентир, Ridge и бустинг.');
  labelled(s,'01','Обучение', 'До 01.04.2026 включительно.\nДаты выпуска с шагом 5 дней.',157);
  labelled(s,'02','Валидация', '02.04–22.07.2026.\nВыбор по MAE, при равенстве по RMSE.',267);
  labelled(s,'03','Отложенный тест', '23.07–11.11.2026.\n335 прогнозов на каждый показатель.',377);
  text(s,'Сезонный ориентир  /  Ridge  /  Градиентный бустинг',77,525,1100,62,{size:28,bold:true,color:C.accent});
}
{
  const s=slide(6,'Ridge показала меньшую ошибку','2:50–3:30. Сравнение проведено на одной отложенной истории для каждого показателя. Поясните, что MAE в процентах загрузки имеет единицу процентные пункты. RMSE придаёт больший вес крупным ошибкам. Ridge уже победила на валидации; тест не использовался в подборе. Источник чисел: build/results.json, выпуск 11.11.2026.');
  const v=[['Модель','Загрузка MAE','Загрузка RMSE','ADR MAE','ADR RMSE']];
  for(let i=0;i<3;i++)v.push([['Сезонная база','Ridge','Бустинг'][i],
    f(occ.finalists[i].test.mae)+' п. п.',f(occ.finalists[i].test.rmse)+' п. п.',
    f(adr.finalists[i].test.mae)+' ₽',f(adr.finalists[i].test.rmse)+' ₽']);
  nativeTable(s,v,183,[248,222,228,210,212]);
  text(s,'–66% MAE загрузки     /     –63% MAE средней цены',76,459,1120,72,{size:38,bold:true,color:C.accent});
  text(s,'Относительно сезонной базы на отложенных синтетических данных.',76,550,1120,48,{size:24,color:C.muted});
}
{
  const top=occ.importance.find(r=>r.name==='Близость площадок');
  const s=slide(7,'Влияние афиши на прогноз','3:30–4:05. Покажите два метода важности: абсолютные вклады и перемешивание. Также есть сравнение между половинами теста и частичная зависимость. Прирост ошибки при исключении признаков ожидаем, так как генератор встраивает событийный эффект.');
  text(s,f(top.native)+' п. п.',76,181,500,112,{size:75,bold:true,color:C.accent});
  text(s,'Средний модуль вклада\nблизости площадок',76,299,505,90,{size:29,bold:true});
  text(s,f(top.permutation)+' п. п.',650,181,525,112,{size:75,bold:true,color:C.accent});
  text(s,'Прирост MAE загрузки\nпри перемешивании признака',650,299,530,90,{size:29,bold:true});
  text(s,'Без признаков афиши MAE загрузки выросла с '+f(occ.winner.test.mae)+' до '+f(occ.without_events.mae)+' п. п.',76,461,1120,55,{size:27});
  text(s,'Симулятор задаёт связь между мероприятиями и спросом.',76,539,1120,47,{size:24,color:C.muted});
}
{
  const s=slide(8,'Демонстрация дашборда','4:05–5:00. Откройте demo/forecast.html или локальный сервер. Выберите «Центр» и 22 ноября. Покажите факторы и анонсы. Затем переключитесь на «Проверку», покажите факт и сезонную базу, смените цель на ADR. При наличии времени раскройте эксперименты и запустите replay. Скриншот: собственная локальная демонстрация.');
  const png=await fs.readFile(path.join(root,'build','qa-dashboard.png'));
  s.images.add({blob:new Uint8Array(png),contentType:'image/png',fit:'contain',alt:'Дашборд с историей, прогнозом и вкладом признаков',
    position:{left:76,top:148,width:745,height:477}});
  text(s,f(peak.occupancy.value,1)+'%',863,175,340,105,{size:76,bold:true,color:C.accent});
  text(s,'Пиковая загрузка\n«Центра» на 22 ноября',867,286,327,90,{size:29,bold:true});
  text(s,'Выбор гостиницы и даты\n\nОбъяснение прогноза\n\nСравнение с фактом',867,399,331,179,{size:22,color:C.muted});
}
{
  const s=slide(9,'Что показал прототип','5:00–5:30. Итог: получен воспроизводимый прототип полного цикла. Уменьшение MAE показано только на симуляции. Чтобы перейти к реальной гостинице, нужны выгрузка PMS, история снимков афиши и повторная оценка. Можно закончить показом ID из журнала и командой воспроизведения.',true);
  text(s,'Прогноз на 14 дней и разбор каждой даты',76,167,1120,84,{size:38,bold:true,color:C.white});
  text(s,'Повторяемые эксперименты по ID',76,275,1120,77,{size:38,bold:true,color:C.white});
  text(s,'Данные и качество проверены на симуляции',76,377,1120,82,{size:38,bold:true,color:C.white});
  text(s,'Следующий шаг: валидация на фактах реальной гостиницы.',76,530,1120,58,{size:26,color:C.light});
}

const draft=path.join(qa,'candidate.pptx');
if(!process.argv.includes('--reuse-draft')){
  for(const [i,s] of P.slides.items.entries()){
    const png=await P.export({slide:s,format:'png',scale:1});
    await fs.writeFile(path.join(qa,'slide-'+(i+1)+'.png'),new Uint8Array(await png.arrayBuffer()));
  }
  await (await PresentationFile.exportPptx(P)).save(draft);
}
const out=path.join(root,'deliverables','Презентация.pptx');
const result=await finalizePresentation({
  workspaceDir:root,candidatePath:draft,finalPath:out,explicitTotalSlideCount:9,
  pythonExecutable:path.join(rt,'python','python.exe'),
  integrityValidatorPath:path.join(skill,'container_tools','inspect_presentation_package_integrity.py'),
  layoutValidatorPath:path.join(skill,'container_tools','inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit','--require-native-table-slide','6'],
  requiredNativeTableOwnerSlides:[6],fontPolicy:{basis:'design',families:[FONT]},
  verifyArtifactToolImport:true,receiptPath:path.join(qa,'validation.json'),
});
console.log(JSON.stringify(result));
