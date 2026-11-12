"use strict";

const element = (id) => document.getElementById(id);
const russianDate = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
});
const moscowTime = new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short", timeStyle: "short", timeZone: "Europe/Moscow",
});
const sourceLabels = {
    event_html: "Учебная",
    public_archive: "Публичный архив",
    kudago_json: "KudaGo",
    mos_afisha: "Мосбилет",
};
const sourceDescriptions = {
    event_html: "Загрузка и цена на 14 дней. Учебные гостиницы и афиша смоделированы.",
    public_archive: "Открытые анонсы организаторов и учебные гостиницы. Архив выборочный. Качество прогноза на нём не измерялось.",
    kudago_json: "Учебные гостиницы и афиша KudaGo на будущие даты. Режим экспериментальный.",
    mos_afisha: "Учебные гостиницы и выборка анонсов московского портала «Мосбилет».",
};
const chartSeries = [
    { key: "fact", color: "#35566b", width: 2 },
    { key: "baseline", color: "#a46d2d", width: 1.8, dashed: true },
    { key: "model", color: "#0c8a77", width: 3 },
];

let data = JSON.parse(element("payload").textContent);
let view = "future";
let selectedIndex = 0;

function formatNumber(value, digits = 1) {
    if (value == null) return "—";
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(value);
}

function dateLabel(date) {
    return russianDate.format(new Date(`${date}T00:00:00Z`));
}

function escapeHtml(value) {
    const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return String(value ?? "").replace(/[&<>"']/g, (character) => entities[character]);
}

function table(headers, rows, highlight = () => false) {
    const headings = headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("");
    const contents = rows.map((row, index) => {
        const cells = row.map((value) => `<td>${escapeHtml(value)}</td>`).join("");
        const rowClass = highlight(index) ? ' class="win"' : "";
        return `<tr${rowClass}>${cells}</tr>`;
    }).join("");
    return `<div class="scroller"><table><thead><tr>${headings}</tr></thead>
        <tbody>${contents}</tbody></table></div>`;
}

function eventLink(event) {
    const title = escapeHtml(event.title);
    if (typeof event.url !== "string" || !event.url.startsWith("https://")) return title;
    return `<a href="${escapeHtml(event.url)}" target="_blank" rel="noopener noreferrer">${title}</a>`;
}

function availabilityLabel(event) {
    const basis = event.basis === "published" ? "опубликовано" : "зафиксировано";
    return `${basis} ${escapeHtml(event.available_from)}`;
}

function configureFilters() {
    const hotel = element("hotel");
    const eventMode = element("eventMode");
    const previousHotel = hotel.value;
    const previousSource = eventMode.value;
    hotel.replaceChildren(...data.hotels.map(({ id, name }) => new Option(name, id)));
    eventMode.replaceChildren(
        ...Object.keys(data.forecasts).map((source) => new Option(sourceLabels[source], source)),
    );
    if (data.hotels.some(({ id }) => id === previousHotel)) hotel.value = previousHotel;
    eventMode.value = Object.hasOwn(data.forecasts, previousSource) ? previousSource : data.event_source;

    element("archivePanel").hidden = !data.archive;
    const archiveDay = element("archiveDay");
    archiveDay.max = data.as_of;
    if (!archiveDay.value || archiveDay.value > data.as_of) archiveDay.value = data.as_of;
    renderArchive();
    renderRates();
    renderRealAdr();
    renderDashboard();
}

function renderRates() {
    const groups = data.market_rates || [];
    element("ratesPanel").hidden = groups.length === 0;
    element("rates").innerHTML = `<p>Опубликованные цены за номер на сутки для двух гостей.
        Наличие номера и стоимость на конкретную дату уточняются при бронировании.
        Прогноз ADR выше рассчитан по учебным данным.</p>`
        + groups.map((group) => {
            const hotel = eventLink({ title: group.hotel, url: group.url });
            if (group.status === "unavailable") return `<p>${hotel} · источник недоступен</p>`;
            const time = moscowTime.format(new Date(group.checked_at));
            const status = group.status === "live" ? "Загружено с сайта" : "Сохранённый снимок";
            const rates = group.rates.map((rate) => [
                rate.room,
                `${formatNumber(rate.price_rub, 0)} ₽`,
                rate.condition,
                rate.valid_to ? `${rate.valid_from} — ${rate.valid_to}` : `с ${rate.valid_from}`,
            ]);
            return `<h3>${hotel}</h3><p>${status} · ${time} мск</p>
                ${table(["Номер", "За сутки", "Условия", "Период по прайсу"], rates)}`;
        }).join("");
}

function renderRealAdr() {
    const result = data.real_adr_validation;
    element("realAdrPanel").hidden = !result;
    if (!result) return;
    const paper = eventLink({ title: "Набор Hotel Booking Demand", url: result.paper });
    const hotels = { H1: "Курортный отель, Алгарве", H2: "Городской отель, Лиссабон" };
    const rows = Object.entries(result.hotels).map(([id, score]) => [
        hotels[id], score.days, formatNumber(score.ridge.mae, 2), formatNumber(score.seasonal.mae, 2),
        formatNumber(score.ridge.rmse, 2), formatNumber(score.seasonal.rmse, 2),
    ]);
    element("realAdr").innerHTML = `<p>${paper}. ${result.windows} выпусков по ${result.horizon} дней,
        ${escapeHtml(result.test_start)} — ${escapeHtml(result.test_end)}.
        Суточная ADR восстановлена по завершённым бронированиям. Ошибки приведены в денежных единицах набора.</p>
        ${table(["Отель", "Дней", "MAE Ridge", "MAE сезонной медианы",
        "RMSE Ridge", "RMSE сезонной медианы"], rows)}
        <p>В этой проверке использованы календарь и история ADR. Для оценки точности на московских
        гостиницах нужны их фактические продажи и выручка.</p>`;
}

function renderArchive() {
    if (!data.archive) return;
    const cutoff = element("archiveDay").value || data.as_of;
    const events = data.archive.events.filter((event) => event.available_from <= cutoff);
    element("archiveNote").textContent = `Снимок от ${data.archive.snapshot}. Фильтр меняет список анонсов.
        Для страниц без даты публикации указана дата первого наблюдения.`;
    if (events.length === 0) {
        element("archive").textContent = "На выбранную дату в архиве нет доступных анонсов.";
        return;
    }
    const items = events.map((event) => `<li>${eventLink(event)} · ${escapeHtml(event.venue)} ·
        ${escapeHtml(event.start)} — ${escapeHtml(event.end)}
        <span class="micro">(${availabilityLabel(event)})</span></li>`);
    element("archive").innerHTML = `<ul>${items.join("")}</ul>`;
}

function drawChart(rows, { selected, forecastStart, unit }) {
    const width = 760;
    const height = 286;
    const left = 62;
    const right = 25;
    const top = 19;
    const bottom = 34;
    const values = rows.flatMap((row) => chartSeries.map(({ key }) => row[key])).filter(Number.isFinite);
    if (values.length === 0) return "";

    const minValue = Math.min(...values);
    const maxValue = Math.max(...values);
    const padding = Math.max((maxValue - minValue) * 0.12, 1);
    const lower = Math.max(0, minValue - padding);
    const upper = Math.min(unit === "%" ? 100 : Infinity, maxValue + padding);
    const chartHeight = height - top - bottom;
    const step = (width - left - right) / Math.max(1, rows.length - 1);
    const x = (index) => left + index * step;
    const y = (value) => top + (upper - value) * chartHeight / (upper - lower);
    const parts = [];

    if (forecastStart !== null) {
        const startX = x(forecastStart) - step / 2;
        parts.push(`<rect x="${startX}" y="${top}" width="${width - right - startX}"
            height="${chartHeight}" rx="4" fill="#e8f5eb"/>`);
    }
    for (let tick = 0; tick <= 4; tick++) {
        const value = lower + (upper - lower) * tick / 4;
        parts.push(`<path d="M ${left} ${y(value)} H ${width - right}" stroke="#e0e9e4"/>
            <text x="${left - 8}" y="${y(value) + 4}" text-anchor="end">${formatNumber(value, 0)}</text>`);
    }
    for (let tick = 0; tick <= 5; tick++) {
        const index = Math.round(tick * (rows.length - 1) / 5);
        parts.push(`<text x="${x(index)}" y="${height - 8}" text-anchor="middle">${dateLabel(rows[index].date)}</text>`);
    }
    parts.push(`<path d="M ${x(selected)} ${top} V ${height - bottom}" stroke="#7b9c91" stroke-dasharray="4 4"/>`);

    for (const { key, color, width: lineWidth, dashed } of chartSeries) {
        let penDown = false;
        const commands = [];
        rows.forEach((row, index) => {
            const value = row[key];
            if (!Number.isFinite(value)) {
                penDown = false;
                return;
            }
            commands.push(`${penDown ? "L" : "M"} ${x(index)} ${y(value)}`);
            penDown = true;
        });
        const dash = dashed ? 'stroke-dasharray="6 5"' : "";
        parts.push(`<path d="${commands.join(" ")}" fill="none" stroke="${color}" stroke-width="${lineWidth}"
            ${dash} stroke-linejoin="round" stroke-linecap="round"/>`);
        const value = rows[selected][key];
        if (Number.isFinite(value)) {
            parts.push(`<circle cx="${x(selected)}" cy="${y(value)}" r="4.5"
                fill="${color}" stroke="white" stroke-width="2"/>`);
        }
    }
    const firstSelectable = forecastStart ?? 0;
    for (let index = firstSelectable; index < rows.length; index++) {
        const row = rows[index];
        parts.push(`<rect class="pick" data-i="${index - firstSelectable}" x="${x(index) - step / 2}"
            y="${top}" width="${step}" height="${chartHeight}" fill="#0d7e6b" opacity="0">
            <title>${dateLabel(row.date)} · ${formatNumber(row.model, 0)} ${escapeHtml(unit)}</title></rect>`);
    }
    return parts.join("");
}

function selection() {
    const hotelId = element("hotel").value;
    const target = element("target").value;
    const source = element("eventMode").value;
    const forecast = data.forecasts[source][hotelId];
    const pastTest = data.backtest[hotelId][target];
    return { hotelId, target, source, forecast, rows: view === "future" ? forecast : pastTest };
}

function renderSummary(forecast, target, metrics, displayValue, displayError) {
    const average = forecast.reduce((total, row) => total + row[target].value, 0) / forecast.length;
    const peak = forecast.reduce((best, row) => best[target].value > row[target].value ? best : row);
    const cards = [
        ["Среднее за 14 дней", displayValue(average), "По прогнозу выбранной гостиницы"],
        ["Пиковый день", displayValue(peak[target].value), dateLabel(peak.date)],
        ["Ошибка модели на тесте", displayError(metrics.winner.test.mae), "MAE по всем гостиницам"],
    ];
    element("metrics").innerHTML = cards.map(([label, value, caption]) =>
        `<div class="metric"><span>${label}</span><strong>${value}</strong><small>${caption}</small></div>`,
    ).join("");
}

function renderForecastDetails(row, target, winner, displayValue, displayError) {
    const forecast = row[target];
    const modelName = winner.family === "hybrid"
        ? `${selectedIndex < winner.params.split ? "Регрессия Хьюбера" : "Линейная регрессия Ridge"} · день ${selectedIndex + 1}`
        : winner.label;
    const ranked = data.features.map((name, index) => [name, forecast.contributions[index]])
        .sort((first, second) => Math.abs(second[1]) - Math.abs(first[1]));
    const maxContribution = Math.max(...ranked.map(([, value]) => Math.abs(value)), 0.001);
    const driver = ([name, value]) => `<div class="driver"><span>${escapeHtml(name)}</span>
        <span class="track"><i style="width: ${100 * Math.abs(value) / maxContribution}%"></i></span>
        <em>${value > 0 ? "+" : ""}${displayError(value)}</em></div>`;
    const clipping = Math.abs(forecast.clip) > 1e-8
        ? `<p>Ограничение диапазона: ${displayError(forecast.clip)}</p>` : "";
    const events = row.events.map((event) => `<li>${eventLink(event)}
        ${event.venue ? ` · ${escapeHtml(event.venue)}` : ""}
        ${event.basis ? `<br><span class="micro">${availabilityLabel(event)}</span>` : ""}</li>`).join("");
    element("detail").innerHTML = `
        <h2>${dateLabel(row.date)}</h2><div class="val">${displayValue(forecast.value)}</div>
        <div class="micro">${escapeHtml(modelName)}</div>
        <h3>Главные факторы прогноза</h3>
        <p>Отсчёт модели: ${displayValue(forecast.intercept)}. Знак показывает направление вклада.</p>
        ${ranked.slice(0, 4).map(driver).join("")}
        <details class="contributions"><summary>Все вклады</summary>
            <div>${ranked.map(driver).join("")}${clipping}</div>
        </details>
        <h3>Известные мероприятия</h3>
        ${events ? `<ul>${events}</ul>` : "<p>В выбранной афише на эту дату нет доступных записей.</p>"}`;
}

function renderTestDetails(row, displayValue, displayError) {
    element("detail").innerHTML = `
        <h2>${dateLabel(row.date)}</h2><div class="val">${displayValue(row.model)}</div>
        <div class="micro">Прогноз выпущен ${dateLabel(row.origin)}</div>
        <h3>Факт и ошибка</h3>
        <p>Факт: <b>${displayValue(row.fact)}</b><br>
        Абсолютная ошибка: <b>${displayError(Math.abs(row.model - row.fact))}</b><br>
        Сезонная медиана: ${displayValue(row.baseline)}</p>`;
}

function renderQuality(metrics, displayError) {
    const { unit, winner, baseline, finalists, by_horizon: horizons, without_events: withoutEvents } = metrics;
    const difference = baseline.test.mae - winner.test.mae;
    const comparison = Math.abs(difference) < 1e-8
        ? "MAE совпала с сезонной медианой."
        : `MAE ${difference > 0 ? "ниже" : "выше"} на ${displayError(Math.abs(difference))} относительно сезонной медианы.`;
    const modelTable = table(
        ["Модель", `MAE валидация, ${unit}`, `MAE тест, ${unit}`, `RMSE тест, ${unit}`],
        finalists.map((model) => [
            model.label, formatNumber(model.validation.mae, 2),
            formatNumber(model.test.mae, 2), formatNumber(model.test.rmse, 2),
        ]),
        (index) => finalists[index].family === winner.family,
    );
    element("quality").innerHTML = `<p>Модель в рабочем прогнозе: ${escapeHtml(winner.label)}.
        На исходном тесте ${comparison} Метрики показаны по всем гостиницам.</p>
        ${modelTable}
        <p>MAE на днях 1–7: ${displayError(horizons["1–7"].mae)}. На днях 8–14: ${displayError(horizons["8–14"].mae)}.
        MAE той же модели без признаков афиши: ${displayError(withoutEvents.mae)}.</p>`;
}

function renderConfirmation(target, unit) {
    const period = data.confirmation;
    const comparison = period.targets[target];
    const metricRow = (label, { previous, candidate }) => [
        label, formatNumber(previous.mae, 2), formatNumber(candidate.mae, 2),
        formatNumber(previous.rmse, 2), formatNumber(candidate.rmse, 2), candidate.n,
    ];
    const rows = [
        metricRow("Все прогнозы", comparison),
        ...Object.entries(comparison.by_horizon).map(([label, scores]) => metricRow(`Дни ${label}`, scores)),
        ...data.hotels.map(({ id, name }) => metricRow(name, comparison.by_hotel[id])),
    ];
    let outcome = "Комбинация прошла проверку и используется для этого показателя.";
    if (!comparison.promoted) {
        outcome = comparison.candidate.mae >= comparison.previous.mae
            ? "Для этого показателя оставлена Ridge. Средняя абсолютная ошибка комбинации не снизилась."
            : "Для этого показателя оставлена Ridge. RMSE комбинации выросла более чем на 5%.";
    }
    element("confirmation").innerHTML = `<p>Синтетический период ${escapeHtml(period.start)} — ${escapeHtml(period.end)}.
        Хьюбер используется на днях 1–7, Ridge на днях 8–14. Параметры зафиксированы до проверочного периода.</p>
        ${table(["Срез", `MAE Ridge, ${unit}`, `MAE схемы, ${unit}`,
            `RMSE Ridge, ${unit}`, `RMSE схемы, ${unit}`, "Прогнозы"], rows, (index) => index === 0)}
        <p>${outcome} Схема точнее в ${comparison.windows_better} из ${period.origins} окон.</p>`;
}

function renderStress(target, { unit, finalists }) {
    const stress = data.stress;
    const delayed = stress.scenarios.find((scenario) => scenario.late);
    const rows = stress.scenarios.map((scenario) => [
        scenario.label,
        ...finalists.map(({ family }) => formatNumber(scenario.targets[target][family].mae, 2)),
    ]);
    element("stress").innerHTML = `<p>Синтетические факты с ${escapeHtml(stress.start)} по ${escapeHtml(stress.end)}.
        Модели обучены по ${escapeHtml(stress.frozen_through)}. Слабый эффект составляет
        ${formatNumber(100 * stress.spec.event_scale, 0)}% прежнего. При задержке перенесено
        ${delayed.shifted} из ${delayed.eligible} ещё не опубликованных анонсов.</p>
        ${table([`Сценарий · MAE, ${unit}`, ...finalists.map(({ label }) => label)], rows)}
        <p>Даты, случайный шум и границы прогноза совпадают во всех сценариях.</p>`;
}

function renderImportance(metrics, displayValue, displayError) {
    const columns = ["native", "permutation", "early", "late"];
    const rows = metrics.importance.slice(0, 8).map((feature) => [
        feature.name, ...columns.map((column) => displayError(feature[column])),
    ]);
    element("importance").innerHTML = `<p>Перемешивание показывает изменение MAE, когда связь отдельного
        признака с остальными наблюдениями нарушается.</p>
        ${table(["Признак", "Средний |вклад|", "Прирост MAE", "|Вклад| 1-я половина", "|Вклад| 2-я половина"], rows)}
        <p>Частичная зависимость показывает отклик модели на изменение индекса близости площадок.</p>
        ${table(["Индекс близости", "Средний прогноз"], metrics.pdp.map((point) => [
            formatNumber(point.value, 2), displayValue(point.prediction),
        ]))}`;
}

function renderLogs({ candidates, winner }) {
    const runs = candidates.map((model) => [
        model.id, model.label, JSON.stringify(model.params),
        formatNumber(model.validation.mae, 2), formatNumber(model.validation.rmse, 2),
    ]);
    const loads = data.journal.map((row) => [
        sourceLabels[row.source] || row.source, row.through_day, row.status, row.added_slices,
        row.rows, row.flagged, row.detail,
    ]);
    element("logs").innerHTML = `<p>Чтобы повторить запуск, выполните <code>python -m hotel.cli replay ID</code>.</p>
        ${table(["ID", "Модель", "Настройки", "MAE", "RMSE"], runs, (index) => candidates[index].id === winner.id)}
        <p>Последние запуски загрузки</p>
        ${table(["Источник", "До даты", "Статус", "Новых порций", "Строк", "Флаги", "Сообщение"], loads)}`;
}

function renderDashboard() {
    const { hotelId, target, source, forecast, rows } = selection();
    const metrics = data.targets[target];
    const digits = target === "occupancy" ? 1 : 0;
    const suffix = target === "occupancy" ? "%" : " ₽";
    const displayValue = (value) => formatNumber(value, digits) + suffix;
    const displayError = (value) => `${formatNumber(value, digits)} ${metrics.unit}`;
    const showForecast = view === "future";

    element("asof").textContent = `Дата расчёта · ${dateLabel(data.as_of)} ${data.as_of.slice(0, 4)}`;
    const modeNote = showForecast
        ? sourceDescriptions[source] : "Отложенный тест на учебных гостиницах и синтетической афише.";
    const online = showForecast && data.online_schedule?.source === source ? data.online_schedule : null;
    const onlineNote = online?.status === "live"
        ? ` Загружено ${moscowTime.format(new Date(online.checked_at))} мск.`
        : online?.status === "fallback" ? " Онлайн-источники недоступны или не вернули событий." : "";
    element("mode").textContent = modeNote + onlineNote;
    element("eventMode").disabled = !showForecast;
    element("future").setAttribute("aria-pressed", showForecast);
    element("test").setAttribute("aria-pressed", !showForecast);
    element("chartTitle").textContent = showForecast ? "План на ближайшие 14 дней" : "Проверка на отложенном периоде";
    element("chartNote").textContent = showForecast
        ? "Последние 28 дней и новый прогноз. Нажмите на дату для объяснения."
        : "Факт и прогноз на датах, не участвовавших в подборе модели.";
    renderSummary(forecast, target, metrics, displayValue, displayError);

    const slider = element("day");
    slider.disabled = rows.length === 0;
    element("download").disabled = slider.disabled;
    if (rows.length === 0) {
        element("mainChart").replaceChildren();
        element("selectedDate").textContent = "—";
        element("detail").textContent = "Для выбранной гостиницы и показателя нет наблюдений.";
    } else {
        selectedIndex = Math.max(0, Math.min(selectedIndex, rows.length - 1));
        const selected = rows[selectedIndex];
        const history = data.history[hotelId].slice(-28).map((row) => ({ date: row.date, fact: row[target] }));
        const chartRows = showForecast ? [...history, ...forecast.map((row) => ({
            date: row.date, model: row[target].value, baseline: row[target].baseline,
        }))] : rows;
        element("mainChart").innerHTML = drawChart(chartRows, {
            selected: selectedIndex + (showForecast ? history.length : 0),
            forecastStart: showForecast ? history.length : null,
            unit: suffix.trim(),
        });
        slider.max = rows.length - 1;
        slider.value = selectedIndex;
        slider.setAttribute("aria-valuetext", dateLabel(selected.date));
        element("selectedDate").textContent = dateLabel(selected.date);
        if (showForecast) {
            renderForecastDetails(selected, target, metrics.winner, displayValue, displayError);
        } else {
            renderTestDetails(selected, displayValue, displayError);
        }
    }
    renderQuality(metrics, displayError);
    renderConfirmation(target, metrics.unit);
    renderStress(target, metrics);
    renderImportance(metrics, displayValue, displayError);
    renderLogs(metrics);
    element("footer").textContent = `Версия ${data.version} · Данные ${data.data_hash.slice(0, 12)} ·
        Тест по ${data.splits.test_end} · ${data.splits.test_origins} дат выпуска прогноза`;
}

function downloadCsv() {
    const { hotelId, target, rows } = selection();
    const csv = rows.map((row) => {
        const forecast = view === "future" ? row[target].value : row.model;
        const baseline = view === "future" ? row[target].baseline : row.baseline;
        return [row.date, forecast.toFixed(2), baseline.toFixed(2), row.fact?.toFixed(2) ?? ""].join(";");
    });
    csv.unshift("date;forecast;seasonal_median;actual");
    const url = URL.createObjectURL(new Blob(["\ufeff", csv.join("\r\n")], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${hotelId}-${target}-${view}.csv`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function changeView(event) {
    view = event.currentTarget.id;
    selectedIndex = 0;
    renderDashboard();
}

async function refreshResults() {
    try {
        const response = await fetch("/api/data", { cache: "no-store" });
        if (!response.ok) return;
        const next = await response.json();
        const fields = ["as_of", "data_hash", "event_source", "version"];
        if (fields.some((field) => next[field] !== data[field]) || next.journal[0]?.id !== data.journal[0]?.id) {
            data = next;
            configureFilters();
        }
    } catch {
        // Keep showing the last complete result while the local server is unavailable.
    }
}

for (const id of ["hotel", "target"]) {
    element(id).onchange = () => {
        selectedIndex = 0;
        renderDashboard();
    };
}
element("eventMode").onchange = renderDashboard;
element("archiveDay").onchange = renderArchive;
element("future").onclick = changeView;
element("test").onclick = changeView;
element("day").oninput = (event) => {
    selectedIndex = Number(event.currentTarget.value);
    renderDashboard();
};
element("mainChart").onclick = (event) => {
    const datePoint = event.target.closest("[data-i]");
    if (!datePoint) return;
    selectedIndex = Number(datePoint.dataset.i);
    renderDashboard();
};
element("download").onclick = downloadCsv;

configureFilters();
if (["http:", "https:"].includes(location.protocol)) setInterval(refreshResults, 15000);
