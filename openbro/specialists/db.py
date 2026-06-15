"""SQLite schema + seed data for the specialist category tree.

Tree shape: top-level domains (Tech, Science, Health, Arts, ...) with
mid- and leaf-level sub-categories. Each leaf has trigger keywords (for
fast rule routing) and may point to a trained LoRA adapter on disk.

The DB is the single source of truth: router code, training pipeline,
and runtime adapter loading all read from it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from openbro.specialists import CATEGORIES_DB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id              INTEGER PRIMARY KEY,
    parent_id       INTEGER REFERENCES categories(id),
    slug            TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    keywords        TEXT    NOT NULL DEFAULT '',
    -- Path of the trained LoRA adapter relative to ADAPTERS_DIR.
    -- NULL = no specialist yet; router falls back to nearest ancestor
    -- with an adapter, then to base model.
    adapter_path    TEXT,
    -- Where training data comes from: HF dataset id, scraping config,
    -- or "synthetic" for Claude-generated.
    dataset_source  TEXT,
    -- Status: 'pending' (no data yet), 'data_ready', 'training',
    -- 'trained', 'failed'.
    training_status TEXT    NOT NULL DEFAULT 'pending',
    last_trained    TEXT,
    train_loss      REAL,
    n_examples      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_categories_slug   ON categories(slug);

-- Optional: training-run history per category for auditing.
CREATE TABLE IF NOT EXISTS training_runs (
    id              INTEGER PRIMARY KEY,
    category_id     INTEGER REFERENCES categories(id),
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    base_model      TEXT,
    n_examples      INTEGER,
    train_loss      REAL,
    elapsed_seconds REAL,
    output_dir      TEXT,
    notes           TEXT
);
"""


# Top-level domains plus a representative set of sub-categories. Each
# tuple: (slug, parent_slug, display_name, description, keywords).
# Keywords are pipe-separated and used as case-insensitive substring
# matches by the rule router.
_SEED: list[tuple[str, str | None, str, str, str]] = [
    # ─── TECHNOLOGY ───────────────────────────────────────────────
    ("technology", None, "Technology", "Software, hardware, networks, AI.", ""),
    (
        "coding",
        "technology",
        "Coding",
        "General programming.",
        "code|coding|program|script|developer|software|api|library|framework",
    ),
    (
        "coding-python",
        "coding",
        "Python",
        "Python language and ecosystem.",
        "python|pandas|numpy|django|flask|fastapi|pytest|pyenv",
    ),
    (
        "coding-javascript",
        "coding",
        "JavaScript",
        "JavaScript, TypeScript, frontend, Node.",
        "javascript|typescript|js|node|npm|react|vue|angular|svelte|next.js",
    ),
    (
        "coding-web",
        "coding",
        "Web Development",
        "HTML, CSS, web platform, browser APIs.",
        "html|css|web|browser|http|frontend|website|dom|webpack",
    ),
    (
        "coding-backend",
        "coding",
        "Backend",
        "Server-side, APIs, databases.",
        "backend|server|api|rest|graphql|database|sql|postgres|mysql|mongo",
    ),
    (
        "coding-mobile",
        "coding",
        "Mobile Dev",
        "Android, iOS, React Native, Flutter.",
        "android|ios|swift|kotlin|react native|flutter|mobile app",
    ),
    (
        "coding-data",
        "coding",
        "Data Engineering",
        "ETL, pipelines, big data, warehousing.",
        "etl|spark|hadoop|airflow|kafka|data pipeline|data warehouse|dbt",
    ),
    (
        "coding-ai",
        "coding",
        "AI / ML Engineering",
        "ML coding, LLMs, training, inference.",
        "machine learning|ml|llm|pytorch|tensorflow|transformer|fine-tune|lora|gguf",
    ),
    (
        "coding-devops",
        "coding",
        "DevOps",
        "CI/CD, Docker, Kubernetes, cloud.",
        "docker|kubernetes|k8s|ci|cd|jenkins|github actions|terraform|aws|azure|gcp",
    ),
    (
        "coding-shell",
        "coding",
        "Shell / Scripting",
        "Bash, PowerShell, automation scripts.",
        "bash|shell|powershell|zsh|cmd|batch|.sh|.ps1",
    ),
    (
        "cybersecurity",
        "technology",
        "Cybersecurity",
        "Security, hacking, defence.",
        "security|hack|exploit|vulnerability|cve|owasp|penetration|firewall|encryption",
    ),
    (
        "hardware",
        "technology",
        "Hardware",
        "PCs, peripherals, electronics.",
        "hardware|cpu|gpu|ram|motherboard|laptop|electronics|circuit|arduino|raspberry",
    ),
    (
        "system-admin",
        "technology",
        "System Administration",
        "OS admin, Linux, Windows, networking.",
        "linux|windows|ubuntu|debian|server admin|registry|systemd|powershell admin",
    ),
    (
        "networking",
        "technology",
        "Networking",
        "TCP/IP, DNS, routing, VPN.",
        "tcp|ip|dns|vpn|router|switch|firewall|subnet|wireless|wifi",
    ),
    # ─── SCIENCES ─────────────────────────────────────────────────
    ("science", None, "Science", "Natural and formal sciences.", ""),
    (
        "math",
        "science",
        "Mathematics",
        "Math, statistics, logic.",
        "math|algebra|calculus|geometry|statistic|probability|equation|theorem",
    ),
    (
        "physics",
        "science",
        "Physics",
        "Physics and applications.",
        "physics|mechanics|quantum|relativity|thermodynamics|electromagnetism|force",
    ),
    (
        "chemistry",
        "science",
        "Chemistry",
        "Chemistry and reactions.",
        "chemistry|molecule|reaction|element|periodic|acid|base|organic",
    ),
    (
        "biology",
        "science",
        "Biology",
        "Living systems.",
        "biology|cell|dna|protein|evolution|species|ecology|gene",
    ),
    (
        "earth-space",
        "science",
        "Earth & Space",
        "Geo, astronomy, climate.",
        "geology|astronomy|outer space|planet|star|galaxy|climate|weather",
    ),
    (
        "engineering",
        "science",
        "Engineering",
        "Applied sciences.",
        "engineering|mechanical|electrical|civil|aerospace|materials",
    ),
    # ─── HEALTH ───────────────────────────────────────────────────
    ("health", None, "Health & Medicine", "Wellness, conditions, fitness.", ""),
    (
        "health-general",
        "health",
        "General Medicine",
        "Common conditions, symptoms.",
        "health|sick|symptom|fever|cold|headache|pain|doctor|disease|medicine",
    ),
    (
        "health-mental",
        "health",
        "Mental Health",
        "Stress, anxiety, sleep.",
        "mental|anxiety|depress|stress|sleep|insomnia|therapy|meditation|panic",
    ),
    (
        "health-fitness",
        "health",
        "Fitness & Exercise",
        "Workouts, gym, sports performance.",
        "fitness|exercise|workout|gym|cardio|weight lifting|yoga|stretch",
    ),
    (
        "health-nutrition",
        "health",
        "Nutrition",
        "Diet, vitamins, food science.",
        "nutrition|diet|calorie|protein|vitamin|mineral|keto|vegan|fasting",
    ),
    (
        "health-conditions",
        "health",
        "Conditions",
        "Specific diseases.",
        "diabetes|cancer|hypertension|asthma|covid|migraine|arthritis|allergy",
    ),
    # ─── BUSINESS & FINANCE ───────────────────────────────────────
    ("business", None, "Business & Finance", "Money, work, ventures.", ""),
    (
        "finance-personal",
        "business",
        "Personal Finance",
        "Budgeting, saving, taxes.",
        "budget|saving|tax|emi|loan|credit card|insurance|salary|expense",
    ),
    (
        "finance-investing",
        "business",
        "Investing",
        "Stocks, crypto, retirement.",
        "invest|stock|share|mutual fund|sip|crypto|bitcoin|portfolio|dividend|nifty|sensex",
    ),
    (
        "finance-economics",
        "business",
        "Economics",
        "Macro, micro, policy.",
        "economy|inflation|gdp|recession|interest rate|fiscal|monetary|trade",
    ),
    (
        "entrepreneurship",
        "business",
        "Entrepreneurship",
        "Startups, funding, growth.",
        "startup|founder|funding|vc|seed|pitch|business plan|mvp|product market fit",
    ),
    (
        "marketing",
        "business",
        "Marketing",
        "SEO, ads, social, brand.",
        "marketing|seo|ads|advertising|social media|brand|copywriting|funnel|conversion",
    ),
    # ─── ARTS & CREATIVE ──────────────────────────────────────────
    ("arts", None, "Arts & Creative", "Visual, audio, written craft.", ""),
    (
        "photography",
        "arts",
        "Photography",
        "Cameras, editing.",
        "photo|camera|lens|aperture|iso|lightroom|photoshop|portrait",
    ),
    (
        "design",
        "arts",
        "Design",
        "Graphic, UI, product design.",
        "design|figma|ui|ux|graphic design|illustrator|color palette|typography",
    ),
    (
        "music-creation",
        "arts",
        "Music Creation",
        "Production, theory, instruments.",
        "music production|guitar|piano|drum|chord|composition|daw|fl studio|ableton",
    ),
    (
        "writing",
        "arts",
        "Writing",
        "Creative writing, essays, journaling.",
        "writing|essay|novel|poem|story|journal|grammar|prose|character arc",
    ),
    # ─── ENTERTAINMENT ────────────────────────────────────────────
    ("entertainment", None, "Entertainment", "Media to enjoy.", ""),
    (
        "movies-tv",
        "entertainment",
        "Movies & TV",
        "Films, shows, streaming.",
        "movie|film|netflix|prime video|series|tv show|imdb|director|actor",
    ),
    (
        "books",
        "entertainment",
        "Books",
        "Reading, authors, recommendations.",
        "book|novel|author|kindle|reading|library|fiction|non-fiction",
    ),
    (
        "music-listen",
        "entertainment",
        "Music (Listening)",
        "Songs, artists, playlists.",
        "song|spotify|playlist|album|artist|band|genre|concert",
    ),
    (
        "games",
        "entertainment",
        "Games & Gaming",
        "Video games, board games.",
        "game|gaming|playstation|xbox|steam|nintendo|valorant|fortnite|chess|board game",
    ),
    (
        "sports",
        "entertainment",
        "Sports",
        "Cricket, football, etc.",
        "cricket|football|soccer|nba|ipl|world cup|match|tournament|player",
    ),
    # ─── LIFESTYLE ────────────────────────────────────────────────
    ("lifestyle", None, "Lifestyle", "Day-to-day life.", ""),
    (
        "cooking",
        "lifestyle",
        "Cooking & Food",
        "Recipes, techniques.",
        "recipe|cook|food|kitchen|bake|fry|curry|spice|ingredient",
    ),
    (
        "travel",
        "lifestyle",
        "Travel",
        "Destinations, planning.",
        "travel|trip|vacation|flight|hotel|visa|itinerary|backpack|tourist",
    ),
    (
        "home-diy",
        "lifestyle",
        "Home & DIY",
        "Repairs, renovation, tools.",
        "diy|repair|home improvement|plumbing|paint|carpentry|furniture|tool",
    ),
    (
        "parenting",
        "lifestyle",
        "Parenting",
        "Kids, family.",
        "parenting|baby|child|kid|toddler|school|education kids",
    ),
    (
        "pets",
        "lifestyle",
        "Pets & Animals",
        "Care, training.",
        "pet|dog|cat|puppy|kitten|aquarium|bird|hamster|vet",
    ),
    (
        "gardening",
        "lifestyle",
        "Gardening",
        "Plants, soil, growing.",
        "garden|plant|soil|seed|grow|flower|vegetable|fertiliser",
    ),
    (
        "relationships",
        "lifestyle",
        "Relationships",
        "Dating, family, friends.",
        "relationship|dating|couple|marriage|friend|family conflict|breakup",
    ),
    # ─── EDUCATION ────────────────────────────────────────────────
    ("education", None, "Education & Learning", "Studying, skills.", ""),
    (
        "study-techniques",
        "education",
        "Study Techniques",
        "Memory, productivity, exams.",
        "study|exam|memorise|revision|flashcard|pomodoro|notes|cram|gate|jee|neet|upsc",
    ),
    (
        "language-learning",
        "education",
        "Language Learning",
        "Foreign languages.",
        "learn english|learn hindi|spanish|french|german|duolingo|grammar exercise",
    ),
    (
        "academics",
        "education",
        "Academics",
        "School/college topics.",
        "homework|assignment|college|university|professor|degree|phd|gpa",
    ),
    # ─── NEWS ─────────────────────────────────────────────────────
    ("news", None, "News & Current Events", "What is happening.", ""),
    (
        "news-tech",
        "news",
        "Tech News",
        "Industry, releases.",
        "tech news|launched|released|update|new feature|ai news",
    ),
    (
        "news-india",
        "news",
        "India News",
        "India domestic.",
        "india news|modi|delhi|mumbai|election|parliament|bjp|congress",
    ),
    (
        "news-world",
        "news",
        "World News",
        "International.",
        "world news|ukraine|china|usa|geopolitics|war|election",
    ),
    # ─── PHILOSOPHY & RELIGION ────────────────────────────────────
    ("philosophy-religion", None, "Philosophy & Religion", "Belief, ethics, meaning.", ""),
    (
        "philosophy",
        "philosophy-religion",
        "Philosophy",
        "Ethics, logic, metaphysics.",
        "philosophy|ethic|moral|metaphysic|epistemology|stoic|existential|kant|nietzsche",
    ),
    (
        "religion",
        "philosophy-religion",
        "Religion",
        "Faiths, scriptures.",
        "religion|god|hindu|muslim|christian|buddha|sikh|bible|quran|gita|prayer",
    ),
    # ─── HISTORY ──────────────────────────────────────────────────
    (
        "history",
        None,
        "History",
        "Past events, civilisations.",
        "history|historical|empire|war|dynasty|revolution|ancient|medieval|colonial",
    ),
    # ─── LAW & GOVERNMENT ─────────────────────────────────────────
    (
        "law-gov",
        None,
        "Law & Government",
        "Legal, civic.",
        "law|legal|court|judge|rights|contract|police|fir|constitution|fundamental right",
    ),
    # ─── LANGUAGE & LINGUISTICS ───────────────────────────────────
    (
        "language",
        None,
        "Language & Linguistics",
        "Words, meaning, structure.",
        "linguistic|etymology|grammar|translation|meaning of word|vocabulary",
    ),
    # ─── PRODUCTIVITY ─────────────────────────────────────────────
    (
        "productivity",
        None,
        "Productivity",
        "Tasks, scheduling, tools.",
        "todo|task|calendar|meeting|productivity|gtd|time management|email management|note taking",
    ),
    # ─── OPENBRO TOOLS ────────────────────────────────────────────
    (
        "openbro-tools",
        None,
        "OpenBro Tool Use",
        "Identity + tool calls for this agent.",
        (
            "openbro|who are you|kon ho|kya kar sakte|battery|screenshot|"
            "file_search|process_check|system_health|disk space|drive space|"
            "disk usage|c drive|d drive|free space"
        ),
    ),
    # ─── GENERAL CATCH-ALL ────────────────────────────────────────
    ("general", None, "General / Chitchat", "Default if nothing else fits.", ""),
]


def init_db(db_path: str = CATEGORIES_DB) -> sqlite3.Connection:
    """Create the schema and seed the category tree if empty."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM categories")
    if cur.fetchone()[0] > 0:
        return conn

    # Two passes: parents first so child rows can resolve parent_id.
    slug_to_id: dict[str, int] = {}
    for slug, parent, name, desc, kw in _SEED:
        if parent is None:
            cur.execute(
                "INSERT INTO categories (slug, display_name, description, keywords) "
                "VALUES (?, ?, ?, ?)",
                (slug, name, desc, kw),
            )
            slug_to_id[slug] = cur.lastrowid
    for slug, parent, name, desc, kw in _SEED:
        if parent is not None:
            cur.execute(
                "INSERT INTO categories "
                "(parent_id, slug, display_name, description, keywords) "
                "VALUES (?, ?, ?, ?, ?)",
                (slug_to_id[parent], slug, name, desc, kw),
            )
            slug_to_id[slug] = cur.lastrowid

    conn.commit()
    return conn


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Return how many categories are seeded / data-ready / trained."""
    cur = conn.cursor()
    cur.execute("SELECT training_status, COUNT(*) FROM categories GROUP BY training_status")
    return dict(cur.fetchall())
