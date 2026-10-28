# Kids Cartoon Agent

Поиск популярных англоязычных детских мультиков на YouTube → анализ трендов и генерация оригинальных идей/промптов (Claude) → генерация видео по промптам (Kling API или fal.ai) → склейка сцен (ffmpeg).

## Установка
```
pip install -r requirements.txt
cp .env.example .env
```
Ключи в `.env`:
- `ANTHROPIC_API_KEY` — обязателен для промптов.
- `YOUTUBE_API_KEY` — YouTube Data API v3 (просмотры, лайки, фильтр по дате). Без ключа поиск идёт через yt-dlp.
- `VIDEO_PROVIDER=kling` + `KLING_ACCESS_KEY`/`KLING_SECRET_KEY`, либо `VIDEO_PROVIDER=fal` + `FAL_KEY`.

## Команды
```
python agent.py search -q "nursery rhymes" -q "kids cartoon" --limit 25 --days 60
python agent.py prompts --ideas 3 --scenes 6 --theme "learning colors"
python agent.py generate --aspect 16:9
python agent.py generate --idea 0 --aspect 9:16
python agent.py run --ideas 2 --scenes 4
python agent.py run --no-video
```

## Результаты (`output/`)
- `videos.json` — найденные видео, сортировка по просмотрам.
- `plan.json` — тренды, идеи, промпты сцен, озвучка, title/description/tags для YouTube.
- `videos/NN_title/scene_XX.mp4`, `final.mp4`, `meta.json`.

Повторный `generate` пропускает уже скачанные сцены.
