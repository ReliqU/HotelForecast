import fs from 'node:fs/promises';
import path from 'node:path';
import {
    fileURLToPath,
    pathToFileURL
} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const rt = process.env.HOTEL_RUNTIME;
const skill = process.env.HOTEL_SLIDES_SKILL;
if (!rt || !skill) throw new Error('Set HOTEL_RUNTIME and HOTEL_SLIDES_SKILL.');
const moduleDir = path.join(rt, 'node', 'node_modules');
process.env.CODEX_NODE_MODULES_ROOT = moduleDir;
process.env.NODE_PATH = moduleDir;
process.env.RUNTIME_NODE_MODULES = moduleDir;
const {
    Presentation,
    PresentationFile
} = await import(pathToFileURL(path.join(moduleDir, '@oai', 'artifact-tool', 'dist', 'artifact_tool.mjs')).href);
const {
    finalizePresentation
} = await import(pathToFileURL(path.join(skill, 'container_tools', 'artifact_tool_utils.mjs')).href);
const qa = path.resolve(process.env.HOTEL_SLIDES_QA || path.join(root, 'build', 'slides-qa'));
const output = path.resolve(process.env.HOTEL_ARTIFACT_OUT || path.join(root, 'deliverables'));
await fs.mkdir(qa, {
    recursive: true
});
await fs.mkdir(output, {
    recursive: true
});
const data = JSON.parse(await fs.readFile(process.env.HOTEL_RESULTS || path.join(root, 'demo', 'results.json'), 'utf8'));
const f = (v, n = 2) => new Intl.NumberFormat('ru-RU', {
    minimumFractionDigits: n,
    maximumFractionDigits: n
}).format(v);
const occ = data.targets.occupancy,
    adr = data.targets.adr;
const cf = data.confirmation,
    co = cf.targets.occupancy,
    ca = cf.targets.adr;
const st = data.stress,
    weak = st.scenarios.find(s => s.weak && !s.late),
    delayed = st.scenarios.find(s => s.late);
const demoRow = data.forecasts.public_archive.center.find(r => r.date === '2026-11-17');
const { market_rates: marketRates, real_adr_validation: realAdr } = data;
const P = Presentation.create({
    slideSize: {
        width: 1280,
        height: 720
    }
});
const C = {
    ink: '#15313B',
    muted: '#4A626A',
    paper: '#F5F7F2',
    white: '#FFFFFF',
    accent: '#08766C',
    dark: '#15343B',
    light: '#BEEBBE',
    line: '#D7E7DD'
};
const FONT = 'Arial';

function text(s, str, x, y, w, h, {
    size = 30,
    color = C.ink,
    bold = false,
    align = 'left'
} = {}) {
    const sh = s.shapes.add({
        name: str.slice(0, 60),
        geometry: 'textbox',
        position: {
            left: x,
            top: y,
            width: w,
            height: h
        },
        fill: 'none',
        line: {
            fill: 'none',
            width: 0
        }
    });
    sh.text = str;
    sh.text.style = {
        fontFamily: FONT,
        fontSize: size,
        bold,
        color,
        alignment: align,
        verticalAlignment: 'middle',
        insets: {
            left: 0,
            top: 0,
            right: 0,
            bottom: 0
        }
    };
    return sh;
}

function slide(k, title, notes, dark = false) {
    const s = P.slides.add();
    s.background.fill = dark ? C.dark : C.paper;
    const main = dark ? C.white : C.ink,
        accent = dark ? C.light : C.accent;
    text(s, title, 76, 58, 1120, 75, {
        size: 47,
        color: main,
        bold: true
    });
    text(s, 'ГОРИЗОНТ   /   ТЕМА 17', 76, 654, 950, 25, {
        size: 15,
        color: accent,
        bold: true
    });
    text(s, String(k).padStart(2, '0'), 1138, 654, 58, 25, {
        size: 17,
        color: accent,
        bold: true,
        align: 'right'
    });
    s.speakerNotes.text = notes;
    return s;
}

function item(s, name, body, x, y, w = 522) {
    text(s, name, x, y, w, 43, {
        size: 28,
        bold: true,
        color: C.accent
    });
    text(s, body, x, y + 49, w, 94, {
        size: 25
    });
}

function labelled(s, n, title, body, y) {
    text(s, n, 76, y, 60, 52, {
        size: 25,
        bold: true,
        color: C.accent
    });
    text(s, title, 146, y, 350, 52, {
        size: 28,
        bold: true
    });
    text(s, body, 516, y, 677, 65, {
        size: 24,
        color: C.muted
    });
}

function nativeTable(s, values, y, columns, emphasis = 3) {
    const width = columns.reduce((a, b) => a + b, 0);
    const t = s.tables.add({
        rows: values.length,
        columns: columns.length,
        left: 76,
        top: y,
        width,
        height: values.length * 59,
        columnWidths: columns,
        values
    });
    t.styleOptions = {
        headerRow: false,
        bandedRows: false,
        firstColumn: false
    };
    for (let i = 0; i < values.length; i++) {
        t.rows[i].height = 59;
        for (let j = 0; j < columns.length; j++) {
            const c = t.getCell(i, j);
            c.fill = i === 0 ? C.dark : i === emphasis ? '#E0F0E4' : i % 2 ? '#FFFFFF' : '#F0F5EF';
            c.text.style = {
                fontFamily: FONT,
                fontSize: i === 0 ? 20 : 24,
                bold: i === 0 || i === emphasis,
                color: i === 0 ? C.white : C.ink
            };
        }
    }
    t.borders.assign({
        style: 'solid',
        fill: C.line,
        width: 1
    });
} {
    const s = P.slides.add();
    s.background.fill = C.dark;
    text(s, 'НИТУ МИСИС   /   ИНТЕЛЛЕКТУАЛЬНЫЙ АНАЛИЗ ДАННЫХ', 76, 92, 1110, 42, {
        size: 19,
        color: C.light,
        bold: true
    });
    text(s, 'Прогноз загрузки и цены\nгостиничных номеров', 76, 199, 1130, 200, {
        size: 70,
        color: C.white,
        bold: true
    });
    text(s, 'Семестровый проект по теме 17 «Гостиничный бизнес»', 78, 457, 1120, 64, {
        size: 28,
        color: '#DBEDE5'
    });
    text(s, '2026', 78, 624, 1110, 32, {
        size: 20,
        color: C.light,
        bold: true
    });
    s.speakerNotes.text = 'Тема 17 по методическим указаниям НИТУ МИСИС.';
} {
    const s = slide(2, 'Два прогноза на 14 дней', 'Показатели рассчитываются на гостиницу и дату. Загрузка использует доступный фонд, ADR относится к реально проданным номерам. Оценки помогают выбрать даты для внимания. Оптимизация тарифа не входит в задачу.');
    text(s, '3', 76, 171, 160, 130, {
        size: 103,
        bold: true,
        color: C.accent
    });
    text(s, 'учебные гостиницы', 76, 304, 470, 54, {
        size: 31,
        bold: true
    });
    text(s, 'Центр, Экспо и Парк', 76, 367, 470, 53, {
        size: 26,
        color: C.muted
    });
    item(s, 'Загрузка номерного фонда', 'Проданные номеро-ночи / доступные.\nПомогает планировать смены.', 610, 170, 585);
    item(s, 'Средняя цена номера · ADR', 'Выручка / проданные номеро-ночи.\nПомогает проверить план тарифов.', 610, 372, 585);
} {
    const s = slide(3, 'Архитектура и выбор стека', 'Python объединяет загрузку, проверку и расчёт в одной команде. NumPy нужен для матричных вычислений моделей. SQLite хранит данные, снимки и эксперименты в одном локальном файле, поэтому для защиты не нужна отдельная СУБД. Интерфейс на HTML, CSS и JavaScript работает в браузере, а автономный HTML можно показать даже без Python. Учебная история используется для московской модели. Публичные прайсы показываются как рыночный ориентир.');
    item(s, 'Сбор и очистка', 'Учебные CSV и HTML, онлайн-афиша\nи публичные прайсы гостиниц.', 76, 143, 540);
    item(s, 'Хранение и прогноз', 'Исходные снимки, проверенные данные,\nпризнаки, модели и метрики.', 76, 310, 540);
    item(s, 'Представление результата', 'Дашборд с фильтрами и пояснениями.\nДанные для просмотра и выгрузки.', 76, 477, 540);
    item(s, 'Python и NumPy', 'Одна команда запуска и матричные\nвычисления для моделей.', 650, 143, 545);
    item(s, 'SQLite', 'Локальный файл с данными\nи журналами, отдельный сервер не нужен.', 650, 310, 545);
    item(s, 'HTML, CSS и JavaScript', 'Интерактивность в браузере.\nАвтономный снимок работает без сети.', 650, 477, 545);
} {
    const s = slide(4, 'Что модель знает на дату выпуска', 'Лаги и окна рассчитываются от даты выпуска. Наблюдения после выпуска не меняют вектор признаков; это проверено тестом. У анонсов учитывается дата публикации либо первое наблюдение. Координаты учебной афиши преобразуются в показатель близости.');
    item(s, 'История гостиницы', 'Последний факт, лаги 7, 14 и 28 дней,\nскользящие средние и день недели.', 76, 169);
    item(s, 'Календарь', 'День недели, годовой цикл, номер\nдня горизонта и гостиница.', 650, 169, 545);
    item(s, 'Афиша', 'Дата, категория из текста и близость\nплощадки. Только известные анонсы.', 76, 374, 555);
    item(s, 'Защита от утечки', 'Одна функция признаков для теста\nи прогноза. Будущие факты закрыты.', 650, 374, 545);
} {
    const s = slide(5, 'Временное разбиение', 'Валидация и тест содержат по восемь выпусков на 14 дней. Настройки подбираются по валидации. Для теста модели обучены на периоде до 22 июля. Сезонная медиана использует до четырёх наблюдений того же дня недели, доступных к выпуску; проста и устойчивее к выбросам, чем перенос одного дня.');
    labelled(s, '01', 'Обучение', 'До 01.04.2026 включительно.\nДаты выпуска с шагом 5 дней.', 157);
    labelled(s, '02', 'Валидация', '02.04–22.07.2026.\nВыбор по MAE, при равенстве по RMSE.', 267);
    labelled(s, '03', 'Отложенный тест', '23.07–11.11.2026.\n335 прогнозов на каждый показатель.', 377);
    text(s, 'Два сезонных ориентира  /  Ridge  /  Градиентный бустинг', 77, 525, 1100, 62, {
        size: 28,
        bold: true,
        color: C.accent
    });
} {
    const s = slide(6, 'Хьюбер снизил MAE на исходном тесте', 'Хьюбер меньше штрафует редкие крупные остатки. На прежней валидации и исходном тесте он снижает MAE по обеим целям. Но на диагностических датах 4 марта — 23 июня он уступил в среднем и выиграл только на первой неделе. Поэтому отдельно проверяем схему Хьюбер 1–7 и Ridge 8–14. Источник: результат сборки v1.2.0.');
    const v = [
        ['Модель', 'Загрузка MAE', 'Загрузка RMSE', 'ADR MAE', 'ADR RMSE']
    ];
    for (const [family, label] of [
            ['median', 'Сезонная медиана'],
            ['ridge', 'Ridge'],
            ['robust', 'Хьюбер'],
            ['hybrid', 'Комбинация']
        ]) {
        const [o, a] = [occ, adr].map(t => t.finalists.find(r => r.family === family).test);
        v.push([label, f(o.mae) + ' п. п.', f(o.rmse) + ' п. п.', f(a.mae) + ' ₽', f(a.rmse) + ' ₽']);
    }
    nativeTable(s, v, 158, [248, 222, 228, 210, 212]);
    text(s, 'На новых датах преимущество Хьюбера осталось на днях 1–7.', 76, 484, 1120, 68, {
        size: 32,
        bold: true,
        color: C.accent
    });
    text(s, 'Для итоговой проверки комбинации взят ещё один период.', 76, 566, 1120, 48, {
        size: 24,
        color: C.muted
    });
} {
    const s = slide(7, 'Новая схема улучшила прогноз загрузки', 'Проверка на 12 непересекающихся окнах. Граница 7 дней зафиксирована после диагностического периода, до расчёта этой проверки. Правило замены: ниже MAE и RMSE не выше чем на 5 процентов. Для загрузки выигрыш в 10 из 12 окон, для ADR MAE не снизилась, поэтому оставляем Ridge. Результаты относятся к симуляции.');
    nativeTable(s, [
            ['Показатель', 'MAE Ridge', 'MAE схемы', 'Рабочая модель'],
            ['Загрузка', f(co.previous.mae) + ' п. п.', f(co.candidate.mae) + ' п. п.', 'Хьюбер 1–7 / Ridge 8–14'],
            ['ADR', f(ca.previous.mae) + ' ₽', f(ca.candidate.mae) + ' ₽', 'Ridge']
        ],
        182, [280, 216, 224, 400], 1);
    text(s, 'Точнее в ' + co.windows_better + ' из ' + cf.origins + ' окон по загрузке', 76, 414, 1120, 80, {
        size: 44,
        bold: true,
        color: C.accent
    });
    text(s, 'Подтверждающий период: 24.06–08.12.2027, синтетические факты.', 76, 535, 1120, 49, {
        size: 24,
        color: C.muted
    });
} {
    const s = slide(8, 'Выигрыш зависит от сценария', 'Это отдельный синтетический стресс-период, 12 ноября — 3 марта. Параметры зафиксированы до его начала. Событийный эффект снижен до 35%. Перенесены ' + delayed.shifted + ' из ' + delayed.eligible + ' ещё неизвестных анонсов. Преимущество нового прогноза в обычном режиме не означает выигрыш при сдвиге спроса.');
    const v = [
        ['Сценарий', 'Ridge, п. п.', 'Схема, п. п.', 'Ridge, ₽', 'Схема, ₽']
    ];
    for (const r of st.scenarios) v.push([r.label, ...['occupancy', 'adr'].flatMap(t => ['ridge', 'hybrid'].map(m => f(r.targets[t][m].mae)))]);
    nativeTable(s, v, 158, [350, 188, 200, 181, 201]);
    text(s, 'При слабом эффекте MAE загрузки: ' + f(weak.targets.occupancy.hybrid.mae) + ' против ' + f(weak.targets.occupancy.ridge.mae) + ' п. п.', 76, 486, 1120, 65, {
        size: 33,
        bold: true,
        color: C.accent
    });
    text(s, 'MAE за 12.11.2026–03.03.2027. Модели заморожены до начала периода.', 76, 562, 1120, 49, {
        size: 24,
        color: C.muted
    });
} {
    const s = slide(9, 'Онлайн-афиша и локальный архив', 'При запуске программа пытается получить анонсы на ближайшие 14 дней. Ошибка или пустая выборка переключает источник. Онлайн-анонс считается доступным с даты получения. В архиве для CleanExpo дата доступности взята из датированной новости, а для БИОТ зафиксировано первое наблюдение.\n\nИсточники\nhttps://docs.kudago.com/api/\nhttps://bilet.mos.ru/\n' + data.archive.events.map(e => e.title + ' — ' + e.url).join('\n'));
    labelled(s, '01', 'KudaGo', 'Первый запрос к онлайн-афише Москвы.', 159);
    labelled(s, '02', 'Мосбилет', 'Резерв при сбое или пустой выборке KudaGo.', 267);
    labelled(s, '03', 'Локальный архив', 'Пять сохранённых анонсов на случай\nотказа обоих онлайн-источников.', 375);
    text(s, '12 ноября 2026 года Мосбилет загрузил 6 событий после HTTP 403 от KudaGo.', 76, 514, 1120, 75, {
        size: 27,
        bold: true,
        color: C.accent
    });
} {
    const s = slide(10, 'Публичные цены гостиниц', 'Данные извлекаются из таблиц открытых прайсов. При контрольной загрузке 12 ноября получено 10 тарифов. У каждой строки есть категория, условия, период по прайсу, URL и время получения снимка. Если сайт недоступен, отображается предыдущий снимок. Прайс показывает цену предложения, ADR рассчитывается из выручки и проданных номеро-ночей. Итоговая цена и наличие на конкретную дату определяются при бронировании.\n\nИсточники\n' + marketRates.map(group => group.hotel + ' — ' + group.url).join('\n'));
    const standardRates = marketRates.flatMap(group => group.rates.filter(rate => rate.room === 'Стандарт')
        .map(rate => [group.hotel, rate.condition, f(rate.price_rub, 0) + ' ₽']));
    text(s, 'Стандартный номер на двоих по прайсам, полученным 12.11.2026', 76, 150, 1120, 48, {
        size: 24,
        color: C.muted
    });
    nativeTable(s, [['Гостиница', 'Условия', 'За сутки'], ...standardRates], 222, [326, 504, 290], -1);
    text(s, 'Сайт недоступен — показываем сохранённый тариф и дату его получения.', 76, 495, 1120, 58, {
        size: 24,
        color: C.muted
    });
    text(s, 'Прайс — цена предложения. ADR — выручка на проданную номеро-ночь.', 76, 559, 1120, 55, {
        size: 25,
        bold: true,
        color: C.accent
    });
} {
    const s = slide(11, 'Проверка Ridge на реальных бронированиях', 'Для двух реальных гостиниц Португалии восстановили суточную ADR по завершённым платным бронированиям. Среднюю цену бронирования отнесли на каждую его ночь. В историю на дату выпуска включали только значения, для которых все учтённые гости выехали. Ridge с α = 1 переобучалась перед каждым окном по календарю и истории ADR. Сезонная медиана получает ту же доступную историю. В каждом отеле оценены 112 дат. Ошибки в денежных единицах исходного набора. Для оценки московской модели и влияния афиши нужны суточные факты местных отелей.\n\nИсточник данных\n' + realAdr.paper);
    const hotelLabels = { H1: 'Курортный, Алгарве', H2: 'Городской, Лиссабон' };
    const results = Object.entries(realAdr.hotels).map(([id, row]) => [
        hotelLabels[id], f(row.seasonal.mae), f(row.ridge.mae), f(row.seasonal.rmse), f(row.ridge.rmse)
    ]);
    text(s, '8 прогнозов по 14 дней на отель · 10.04–30.07.2017', 76, 153, 1120, 55, {
        size: 25,
        color: C.muted
    });
    nativeTable(s, [['Гостиница', 'MAE медианы', 'MAE Ridge', 'RMSE медианы', 'RMSE Ridge'], ...results],
        236, [340, 195, 175, 220, 190], -1);
    text(s, 'Ridge снизила MAE и RMSE в обоих отелях.', 76, 461, 1120, 52, {
        size: 29,
        bold: true,
        color: C.accent
    });
    text(s, 'Ошибки в денежных единицах набора Hotel Booking Demand.\nВ проверке использованы календарь и история ADR.', 76, 529, 1120, 70, {
        size: 23,
        color: C.muted
    });
} {
    const s = slide(12, 'Демонстрация дашборда', 'Снимок дашборда в архивном режиме на 17.11.2026. В правой панели видны вклады признаков и два известных анонса. Ниже показаны опубликованные тарифы.');
    const png = await fs.readFile(path.join(root, 'build', 'qa-archive.png'));
    s.images.add({
        blob: new Uint8Array(png),
        contentType: 'image/png',
        fit: 'contain',
        alt: 'Дашборд с историей, прогнозом и вкладом признаков',
        position: {
            left: 76,
            top: 148,
            width: 745,
            height: 477
        }
    });
    text(s, String(demoRow.events.length), 863, 174, 340, 105, {
        size: 76,
        bold: true,
        color: C.accent
    });
    text(s, 'публичных анонса\nна 17 ноября', 867, 286, 327, 90, {
        size: 29,
        bold: true
    });
    text(s, 'Источник афиши\n\nВклады признаков\n\nПубличные цены и проверка Ridge', 867, 399, 331, 193, {
        size: 22,
        color: C.muted
    });
} {
    const s = slide(13, 'Результаты проекта', 'На симуляции комбинация Хьюбера и Ridge улучшила прогноз загрузки. Ridge улучшила прогноз ADR по сравнению с сезонной медианой в отдельной проверке на бронированиях двух отелей. Для оценки точности московского прогноза нужны факты из систем местных гостиниц.', true);
    text(s, 'Комбинация моделей снизила MAE загрузки на симуляции', 76, 165, 1120, 84, {
        size: 35,
        bold: true,
        color: C.white
    });
    text(s, 'Ridge точнее сезонной медианы на данных двух отелей', 76, 272, 1120, 84, {
        size: 35,
        bold: true,
        color: C.white
    });
    text(s, 'Дашборд загружает афишу и цены с резервом', 76, 378, 1120, 84, {
        size: 35,
        bold: true,
        color: C.white
    });
    text(s, 'Для проверки московского прогноза нужны суточные данные местных гостиниц.', 76, 530, 1120, 58, {
        size: 26,
        color: C.light
    });
}

const draft = path.join(qa, 'candidate.pptx');
if (!process.argv.includes('--reuse-draft')) {
    for (const [i, s] of P.slides.items.entries()) {
        const png = await P.export({
            slide: s,
            format: 'png',
            scale: 1
        });
        await fs.writeFile(path.join(qa, 'slide-' + (i + 1) + '.png'), new Uint8Array(await png.arrayBuffer()));
    }
    await (await PresentationFile.exportPptx(P)).save(draft);
}
const out = path.join(output, 'Презентация.pptx');
const result = await finalizePresentation({
    workspaceDir: root,
    candidatePath: draft,
    finalPath: out,
    explicitTotalSlideCount: 13,
    pythonExecutable: path.join(rt, 'python', 'python.exe'),
    integrityValidatorPath: path.join(skill, 'container_tools', 'inspect_presentation_package_integrity.py'),
    layoutValidatorPath: path.join(skill, 'container_tools', 'inspect_presentation_layout_geometry.py'),
    layoutArgs: ['--expected-slide-size-emu', '12192000,6858000', '--validate-bullet-geometry', '--validate-heading-fit',
        ...[6, 7, 8, 10, 11].flatMap(number => ['--require-native-table-slide', String(number)])],
    requiredNativeTableOwnerSlides: [6, 7, 8, 10, 11],
    fontPolicy: {
        basis: 'design',
        families: [FONT]
    },
    verifyArtifactToolImport: true,
    receiptPath: path.join(qa, 'validation.json'),
});
console.log(JSON.stringify(result));
