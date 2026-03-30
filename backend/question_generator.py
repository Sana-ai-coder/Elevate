import random
import json
import os
from urllib import request as urlrequest
from urllib import error as urlerror
from datetime import datetime, timezone
from typing import Dict, List, Any
from .models import Question, db


def _get_gemini_key() -> str:
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def _get_ai_provider() -> str | None:
    provider = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    if provider:
        return provider
    if _get_gemini_key():
        return "gemini"
    if os.environ.get("AI_API_KEY"):
        return "openai"
    return None


def is_ai_generation_available() -> bool:
    provider = _get_ai_provider()
    if provider == "gemini":
        return bool(_get_gemini_key())
    if provider == "openai":
        return bool(os.environ.get("AI_API_KEY"))
    return False


def _build_ai_generation_prompt(subject: str, grade: str, difficulty: str, count: int) -> str:
    return (
        "Generate high-quality STEM multiple-choice questions as strict JSON only. "
        "Return an array of objects with keys: text, options, correct_index, topic, hint, explanation. "
        "Rules: options must be a list of exactly 4 unique strings; correct_index is 0..3; "
        f"subject={subject}; grade={grade}; difficulty={difficulty}; count={count}. "
        "Keep questions pedagogically sound, age-appropriate, and non-repetitive. "
        "Do not include markdown, prose, or code fences."
    )


def _sanitize_ai_question_item(item: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    text = str(item.get("text", "")).strip()
    options_raw = item.get("options")
    if not text or not isinstance(options_raw, list):
        return None

    options = [str(opt).strip() for opt in options_raw if str(opt).strip()]
    # Preserve option order while removing duplicates.
    deduped = []
    seen = set()
    for opt in options:
        if opt not in seen:
            seen.add(opt)
            deduped.append(opt)
    options = deduped[:4]
    if len(options) < 2:
        return None

    try:
        correct_index = int(item.get("correct_index", 0))
    except (TypeError, ValueError):
        correct_index = 0
    if correct_index < 0 or correct_index >= len(options):
        correct_index = 0

    return {
        "text": text,
        "options": options,
        "correct_index": correct_index,
        "topic": str(item.get("topic", "general")).strip() or "general",
        "hint": str(item.get("hint", "Think carefully about key concepts.")).strip(),
        "explanation": str(item.get("explanation", "")).strip() or "Review the concept and solution steps.",
    }


def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return []

    # Support providers that wrap JSON in markdown code fences.
    if "```" in raw:
        parts = raw.split("```")
        raw = max(parts, key=len).strip() if parts else raw
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return [p for p in parsed if isinstance(p, dict)]
    if isinstance(parsed, dict):
        candidates = parsed.get("questions")
        if isinstance(candidates, list):
            return [p for p in candidates if isinstance(p, dict)]
    return []


def _merge_unique_questions(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    merged = list(existing)
    seen_texts = {str(item.get("text", "")).strip().lower() for item in merged if item.get("text")}
    for item in incoming:
        key = str(item.get("text", "")).strip().lower()
        if not key or key in seen_texts:
            continue
        seen_texts.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _generate_openai_stem_questions(subject: str, grade: str, difficulty: str, count: int = 10) -> List[Dict[str, Any]]:
    """Generate questions from an OpenAI-compatible API endpoint."""
    api_key = os.environ.get("AI_API_KEY")
    if not api_key:
        return []

    base_url = os.environ.get("AI_API_BASE", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("AI_MODEL", "gpt-4o-mini")

    payload = {
        "model": model,
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert STEM educator. Return JSON only with a top-level key 'questions'. "
                    "'questions' must be an array of question objects."
                ),
            },
            {
                "role": "user",
                "content": _build_ai_generation_prompt(subject, grade, difficulty, count),
            },
        ],
    }

    req = urlrequest.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urlrequest.urlopen(req, timeout=25) as response:
            body = response.read().decode("utf-8")
    except (urlerror.HTTPError, urlerror.URLError, TimeoutError, OSError):
        return []

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []

    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices or not isinstance(choices, list):
        return []

    content = (((choices[0] or {}).get("message") or {}).get("content") or "").strip()
    try:
        parsed_items = _extract_json_array(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []

    normalized_items = []
    for item in parsed_items:
        cleaned = _sanitize_ai_question_item(item)
        if cleaned:
            normalized_items.append(cleaned)
        if len(normalized_items) >= count:
            break
    return normalized_items


def _generate_gemini_stem_questions(subject: str, grade: str, difficulty: str, count: int = 10) -> List[Dict[str, Any]]:
    """Generate questions using Gemini generateContent REST API."""
    api_key = _get_gemini_key()
    if not api_key:
        return []

    configured_model = (os.environ.get("GEMINI_MODEL") or "").strip()
    model_candidates = [configured_model] if configured_model else ["gemini-1.5-flash", "gemini-2.0-flash"]
    prompt = _build_ai_generation_prompt(subject, grade, difficulty, count)
    payload = {
        "system_instruction": {
            "parts": [
                {
                    "text": (
                        "You are an expert STEM educator. Return JSON only with a top-level key 'questions'. "
                        "'questions' must be an array of question objects."
                    )
                }
            ]
        },
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json",
        },
    }

    for model in model_candidates:
        req = urlrequest.Request(
            (
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                f"?key={api_key}"
            ),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlrequest.urlopen(req, timeout=25) as response:
                body = response.read().decode("utf-8")
        except (urlerror.HTTPError, urlerror.URLError, TimeoutError, OSError):
            continue

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue

        candidates = data.get("candidates") if isinstance(data, dict) else None
        if not candidates or not isinstance(candidates, list):
            continue

        parts = ((((candidates[0] or {}).get("content") or {}).get("parts")) or [])
        text_chunks = [part.get("text", "") for part in parts if isinstance(part, dict)]
        content = "\n".join([chunk for chunk in text_chunks if chunk]).strip()
        if not content:
            continue

        try:
            parsed_items = _extract_json_array(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

        normalized_items = []
        for item in parsed_items:
            cleaned = _sanitize_ai_question_item(item)
            if cleaned:
                normalized_items.append(cleaned)
            if len(normalized_items) >= count:
                break
        if normalized_items:
            return normalized_items

    return []


def generate_ai_stem_questions(subject: str, grade: str, difficulty: str, count: int = 10) -> List[Dict[str, Any]]:
    """Generate questions from configured AI provider.

    Provider selection:
    - AI_PROVIDER=gemini with GEMINI_API_KEY or GOOGLE_API_KEY (default when either key exists)
    - AI_PROVIDER=openai with AI_API_KEY (or auto when AI_API_KEY exists)

    Optional:
    - GEMINI_MODEL (default: gemini-2.0-flash)
    - AI_API_BASE (default: https://api.openai.com/v1)
    - AI_MODEL (default: gpt-4o-mini)
    """
    provider = _get_ai_provider()
    if provider not in {"gemini", "openai"}:
        return []

    collected: List[Dict[str, Any]] = []
    max_attempts = 4
    for _ in range(max_attempts):
        remaining = count - len(collected)
        if remaining <= 0:
            break

        # Ask for a couple extra each attempt to offset occasional invalid/duplicate items.
        request_count = min(remaining + 2, max(count, 12))
        if provider == "gemini":
            batch = _generate_gemini_stem_questions(subject, grade, difficulty, request_count)
        else:
            batch = _generate_openai_stem_questions(subject, grade, difficulty, request_count)

        collected = _merge_unique_questions(collected, batch, count)
        if len(collected) >= count:
            break

    return collected[:count]

class STEMQuestionGenerator:
    """Rule-based STEM question generator for various subjects and grade levels."""
    
    # Math templates by grade and difficulty
    MATH_TEMPLATES = {
        'elementary': {
            'easy': [
                {
                    'template': "What is {a} + {b}?",
                    'answer_func': lambda a, b: a + b,
                    'topic': 'addition',
                    'options_func': lambda a, b, correct: [
                        correct, correct + 1, correct - 1, correct + 2
                    ]
                },
                {
                    'template': "What is {a} - {b}?",
                    'answer_func': lambda a, b: a - b,
                    'topic': 'subtraction',
                    'options_func': lambda a, b, correct: [
                        correct, correct + 1, correct - 1, correct + 2
                    ]
                },
                {
                    'template': "What is {a} × {b}?",
                    'answer_func': lambda a, b: a * b,
                    'topic': 'multiplication',
                    'options_func': lambda a, b, correct: [
                        correct, correct + a, correct + b, correct * 2
                    ]
                }
            ],
            'medium': [
                {
                    'template': "What is {a} ÷ {b}?",
                    'answer_func': lambda a, b: a // b,
                    'topic': 'division',
                    'options_func': lambda a, b, correct: [
                        correct, correct + 1, correct - 1, correct * 2
                    ]
                },
                {
                    'template': "What is {a}% of {b}?",
                    'answer_func': lambda a, b: (a * b) // 100,
                    'topic': 'percentages',
                    'options_func': lambda a, b, correct: [
                        correct, correct + 5, correct - 5, correct * 2
                    ]
                }
            ],
            'hard': [
                {
                    'template': "Solve for x: {a}x + {b} = {c}",
                    'answer_func': lambda a, b, c: (c - b) // a,
                    'topic': 'linear_equations',
                    'options_func': lambda a, b, c, correct: [
                        correct, correct + 1, correct - 1, correct * 2
                    ]
                }
            ]
        },
        'middle': {
            'easy': [
                {
                    'template': "What is the square root of {a}?",
                    'answer_func': lambda a: int(a ** 0.5),
                    'topic': 'square_roots',
                    'options_func': lambda a, correct: [
                        correct, correct + 1, correct - 1, correct * 2
                    ]
                }
            ],
            'medium': [
                {
                    'template': "What is {a}²?",
                    'answer_func': lambda a: a ** 2,
                    'topic': 'exponents',
                    'options_func': lambda a, correct: [
                        correct, correct + a, correct - a, correct + 10
                    ]
                },
                {
                    'template': "Find the area of a square with side {a} units.",
                    'answer_func': lambda a: a ** 2,
                    'topic': 'geometry',
                    'options_func': lambda a, correct: [
                        correct, correct + a, correct * 2, correct + 5
                    ]
                }
            ],
            'hard': [
                {
                    'template': "Solve the quadratic equation: x² + {a}x + {b} = 0",
                    'answer_func': lambda a, b: "Use quadratic formula",
                    'topic': 'quadratic_equations',
                    'options_func': lambda a, b, correct: [
                        "Use quadratic formula", "Factor", "Complete square", "Graph"
                    ]
                }
            ]
        },
        'high': {
            'easy': [
                {
                    'template': "What is sin({a}°)?",
                    'answer_func': lambda a: round(__import__('math').sin(__import__('math').radians(a)), 2),
                    'topic': 'trigonometry',
                    'options_func': lambda a, correct: [
                        correct, round(correct, 1), round(correct, 0), correct + 0.1
                    ]
                }
            ],
            'medium': [
                {
                    'template': "Find the derivative of f(x) = {a}x^{b}",
                    'answer_func': lambda a, b: f"{a*b}x^{b-1}",
                    'topic': 'calculus',
                    'options_func': lambda a, b, correct: [
                        correct, f"{a}x^{b}", f"{a*b}x^{b}", f"{a}x^{b-1}"
                    ]
                }
            ],
            'hard': [
                {
                    'template': "Evaluate the integral: ∫{a}x^{b} dx",
                    'answer_func': lambda a, b: f"{a/(b+1)}x^{b+1} + C",
                    'topic': 'integration',
                    'options_func': lambda a, b, correct: [
                        correct, f"{a}x^{b+1} + C", f"{a/(b+1)}x^{b} + C", f"{a*b}x^{b+1} + C"
                    ]
                }
            ]
        }
    }
    
    # Science templates
    SCIENCE_TEMPLATES = {
        'elementary': {
            'easy': [
                {
                    'template': "What is the chemical symbol for {element}?",
                    'answer_func': lambda element: ELEMENTS.get(element, "H"),
                    'topic': 'elements',
                    'options_func': lambda element, correct: [
                        correct, "H", "O", "C"
                    ]
                }
            ],
            'medium': [
                {
                    'template': "What is the process by which plants make their own food?",
                    'answer_func': lambda: "Photosynthesis",
                    'topic': 'photosynthesis',
                    'options_func': lambda correct: [
                        correct, "Respiration", "Transpiration", "Germination"
                    ]
                }
            ]
        },
        'middle': {
            'easy': [
                {
                    'template': "What is the force that pulls objects toward the center of Earth?",
                    'answer_func': lambda: "Gravity",
                    'topic': 'gravity',
                    'options_func': lambda correct: [
                        correct, "Magnetism", "Friction", "Buoyancy"
                    ]
                }
            ],
            'medium': [
                {
                    'template': "What is the chemical formula for water?",
                    'answer_func': lambda: "H₂O",
                    'topic': 'chemical_formulas',
                    'options_func': lambda correct: [
                        correct, "CO₂", "O₂", "H₂O₂"
                    ]
                }
            ]
        },
        'high': {
            'easy': [
                {
                    'template': "What is Newton's second law of motion?",
                    'answer_func': lambda: "F = ma",
                    'topic': 'physics_laws',
                    'options_func': lambda correct: [
                        correct, "F = mv", "E = mc²", "PV = nRT"
                    ]
                }
            ],
            'medium': [
                {
                    'template': "What is the acceleration due to gravity on Earth?",
                    'answer_func': lambda: "9.8 m/s²",
                    'topic': 'gravity',
                    'options_func': lambda correct: [
                        correct, "8.9 m/s²", "10.8 m/s²", "9.0 m/s²"
                    ]
                }
            ]
        }
    }
    
    ELEMENTS = {
        'Hydrogen': 'H',
        'Oxygen': 'O',
        'Carbon': 'C',
        'Nitrogen': 'N',
        'Iron': 'Fe',
        'Gold': 'Au',
        'Silver': 'Ag',
        'Copper': 'Cu'
    }
    
    @classmethod
    def generate_math_question(cls, grade: str, difficulty: str, rng: random.Random | None = None) -> Dict[str, Any]:
        """Generate a math question based on grade and difficulty using an RNG instance for determinism."""
        rng = rng or random
        templates = cls.MATH_TEMPLATES.get(grade, {}).get(difficulty, [])
        if not templates:
            templates = cls.MATH_TEMPLATES['elementary']['easy']  # fallback
        
        template = rng.choice(templates)
        
        # Generate random values
        if grade == 'elementary':
            a = rng.randint(1, 10)
            b = rng.randint(1, 10)
            if difficulty == 'hard':
                c = rng.randint(1, 20)
        elif grade == 'middle':
            a = rng.randint(5, 15)
            b = rng.randint(2, 10)
            if difficulty == 'hard':
                c = rng.randint(1, 10)
        else:  # high
            a = rng.randint(1, 5)
            b = rng.randint(2, 4)
            if difficulty == 'hard':
                c = rng.randint(1, 3)
        
        # Generate question and answer
        # Call answer_func and options_func with the right number of args based on their signatures
        import inspect
        question_text = template['template'].format(**{k: v for k, v in [('a', a), ('b', b), ('c', locals().get('c'))] if v is not None})

        answer_fn = template['answer_func']
        opt_fn = template['options_func']

        # Determine how many args answer_fn expects
        try:
            ans_params = len(inspect.signature(answer_fn).parameters)
        except Exception:
            ans_params = 2

        if ans_params == 1:
            correct_answer = answer_fn(a)
        elif ans_params == 2:
            correct_answer = answer_fn(a, b)
        else:
            # assume 3
            correct_answer = answer_fn(a, b, locals().get('c'))

        # Determine options function signature
        try:
            opt_params = len(inspect.signature(opt_fn).parameters)
        except Exception:
            opt_params = 3

        if opt_params == 2:
            options = opt_fn(a, correct_answer)
        elif opt_params == 3:
            options = opt_fn(a, b, correct_answer)
        else:
            options = opt_fn(a, b, locals().get('c'), correct_answer)
        
        # Ensure options are strings and unique
        options = [str(o) for o in options]
        seen = set()
        unique_options = []
        for opt in options:
            if opt not in seen:
                seen.add(opt)
                unique_options.append(opt)

        # If uniqueness caused too few options, add simple distractors
        while len(unique_options) < 2:
            unique_options.append(str(int(correct_answer) + len(unique_options) + 1))

        rng.shuffle(unique_options)
        correct_index = unique_options.index(str(correct_answer)) if str(correct_answer) in unique_options else 0
        
        return {
            'text': question_text,
            'options': unique_options,
            'correct_index': correct_index,
            'topic': template['topic'],
            'hint': cls._generate_hint(template['topic'], grade),
            'explanation': cls._generate_explanation(template['topic'], question_text, correct_answer)
        }
    
    @classmethod
    def generate_science_question(cls, grade: str, difficulty: str, subject: str = 'Science', rng: random.Random | None = None) -> Dict[str, Any]:
        """Generate a science question based on grade and difficulty using RNG for determinism."""
        rng = rng or random
        templates = cls.SCIENCE_TEMPLATES.get(grade, {}).get(difficulty, [])
        if not templates:
            templates = cls.SCIENCE_TEMPLATES['elementary']['easy']  # fallback
        
        template = rng.choice(templates)
        
        # Generate question and answer
        if 'element' in template['template']:
            element = rng.choice(list(cls.ELEMENTS.keys()))
            question_text = template['template'].format(element=element)
            correct_answer = template['answer_func'](element)
            options = template['options_func'](element, correct_answer)
        else:
            question_text = template['template']
            correct_answer = template['answer_func']()
            options = template['options_func'](correct_answer)
        
        # Ensure options are strings and unique
        options = [str(o) for o in options]
        seen = set()
        unique_options = []
        for opt in options:
            if opt not in seen:
                seen.add(opt)
                unique_options.append(opt)

        while len(unique_options) < 2:
            unique_options.append(str(unique_options[-1] + ' alt' if unique_options else 'Choice'))

        rng.shuffle(unique_options)
        correct_index = unique_options.index(str(correct_answer)) if str(correct_answer) in unique_options else 0
        
        return {
            'text': question_text,
            'options': unique_options,
            'correct_index': correct_index,
            'topic': template['topic'],
            'hint': cls._generate_hint(template['topic'], grade),
            'explanation': cls._generate_explanation(template['topic'], question_text, correct_answer)
        }
    
    @classmethod
    def generate_question(cls, subject: str, grade: str, difficulty: str, rng: random.Random | None = None) -> Dict[str, Any]:
        """Generate a question for any STEM subject using RNG (optional)."""
        if subject.lower() in ['math', 'mathematics']:
            return cls.generate_math_question(grade, difficulty, rng=rng)
        elif subject.lower() in ['physics', 'chemistry', 'biology', 'science']:
            return cls.generate_science_question(grade, difficulty, subject, rng=rng)
        else:
            # Default to math for unknown subjects
            return cls.generate_math_question(grade, difficulty, rng=rng)
    
    @classmethod
    def _generate_hint(cls, topic: str, grade: str) -> str:
        """Generate a hint based on the topic and grade level."""
        hints = {
            'addition': "Try counting on your fingers or using a number line.",
            'subtraction': "Think about what you need to add to the smaller number to get the larger one.",
            'multiplication': "This is repeated addition. Try adding the first number multiple times.",
            'division': "Think about how many times the second number fits into the first.",
            'percentages': "Remember that percent means 'out of 100'.",
            'linear_equations': "Isolate x by performing the same operation on both sides.",
            'square_roots': "What number multiplied by itself gives this result?",
            'exponents': "This means multiplying the number by itself this many times.",
            'geometry': "Remember the formula for the area of a square.",
            'trigonometry': "Use the unit circle or trigonometric ratios.",
            'calculus': "Apply the power rule for differentiation.",
            'integration': "Use the reverse of the power rule.",
            'elements': "Think about the periodic table of elements.",
            'photosynthesis': "This process uses sunlight, water, and carbon dioxide.",
            'gravity': "This is the force that keeps us on Earth.",
            'chemical_formulas': "Think about the composition of common substances.",
            'physics_laws': "This relates force, mass, and acceleration."
        }
        return hints.get(topic, "Read the question carefully and think about what you've learned.")
    
    @classmethod
    def _generate_explanation(cls, topic: str, question: str, answer: str) -> str:
        """Generate an explanation for the answer."""
        explanations = {
            'addition': f"To solve this, add the numbers together. The answer is {answer}.",
            'subtraction': f"Subtract the second number from the first. The answer is {answer}.",
            'multiplication': f"Multiply the numbers to get {answer}.",
            'division': f"Divide the first number by the second to get {answer}.",
            'percentages': f"Convert the percentage to a decimal and multiply. The answer is {answer}.",
            'linear_equations': f"Isolate x to get x = {answer}.",
            'square_roots': f"The square root of the number is {answer}.",
            'exponents': f"Raising the number to this power gives {answer}.",
            'geometry': f"Using the area formula, the answer is {answer}.",
            'trigonometry': f"Using trigonometric ratios, the answer is {answer}.",
            'calculus': f"Applying differentiation rules gives {answer}.",
            'integration': f"Integrating gives {answer}.",
            'elements': f"The chemical symbol is {answer}.",
            'photosynthesis': f"The correct answer is {answer}.",
            'gravity': f"The force is {answer}.",
            'chemical_formulas': f"The chemical formula is {answer}.",
            'physics_laws': f"The law is {answer}."
        }
        return explanations.get(topic, f"The correct answer is {answer}.")


def generate_stem_questions(
    subject: str,
    grade: str,
    difficulty: str,
    count: int = 10,
    seed: int | None = None,
    require_ai: bool = False,
    topic: str | None = None,
    variant_offset: int = 0,
) -> List[Dict[str, Any]]:
    """Generate STEM questions using AI first (if configured), then template fallback.

    If require_ai is True, return all available AI questions (can be partial).
    """
    ai_questions = generate_ai_stem_questions(subject, grade, difficulty, count)
    if len(ai_questions) >= count:
        return ai_questions[:count]
    if require_ai:
        return ai_questions

    questions = []
    rng = random.Random(seed) if seed is not None else random
    missing = count - len(ai_questions)
    for idx in range(missing):
        variant_index = variant_offset + idx
        question_data = (
            _generate_topic_fallback_question(subject, grade, difficulty, topic, variant_index, rng)
            if topic
            else STEMQuestionGenerator.generate_question(subject, grade, difficulty, rng=rng)
        )
        questions.append(question_data)
    return ai_questions + questions


def _generate_topic_fallback_question(
    subject: str,
    grade: str,
    difficulty: str,
    topic: str,
    variant_index: int,
    rng: random.Random | random.Random = random,
) -> Dict[str, Any]:
    """Create deterministic topic-aligned fallback questions for non-AI or sparse banks."""
    subject_clean = str(subject or 'general').strip().lower()
    grade_clean = str(grade or 'middle').strip().lower()
    difficulty_clean = str(difficulty or 'medium').strip().lower()
    topic_clean = str(topic or 'general').strip().lower()
    topic_title = " ".join(topic_clean.replace('-', '_').split('_'))

    subject_focus = {
        'technology': 'computing systems and digital tools',
        'engineering': 'design constraints and system performance',
        'science': 'evidence-based scientific reasoning',
        'mathematics': 'quantitative reasoning and mathematical structure',
        'physics': 'physical laws and measurable interactions',
        'chemistry': 'matter, reactions, and molecular behavior',
        'biology': 'living systems and biological processes',
    }.get(subject_clean, f'{subject_clean} fundamentals')

    task_contexts = [
        "quiz system",
        "revision worksheet",
        "project checkpoint",
        "lab activity",
        "practice assessment",
        "classroom simulation",
        "guided exercise",
        "peer-review task",
    ]
    evidence_lenses = [
        "measurable outcomes",
        "validated assumptions",
        "observed behavior",
        "error logs",
        "benchmark results",
        "constraint checks",
        "domain rules",
        "comparison baselines",
    ]
    risk_patterns = [
        "overfitting to familiar examples",
        "ignoring edge cases",
        "using unrelated heuristics",
        "premature optimization",
        "data leakage",
        "unverified assumptions",
        "label noise impact",
        "distribution drift",
    ]

    task_ctx = task_contexts[variant_index % len(task_contexts)]
    evidence_ctx = evidence_lenses[(variant_index // 2) % len(evidence_lenses)]
    risk_ctx = risk_patterns[(variant_index // 3) % len(risk_patterns)]

    if difficulty_clean == 'easy':
        stem = (
            f"In a {task_ctx} for {subject_clean.title()} ({grade_clean}), which statement best introduces {topic_title}?"
        )
        correct = f"It explains the core idea of {topic_title} with a concrete example and clear terminology"
        distractors = [
            f"It avoids {topic_title} and focuses on unrelated facts",
            f"It treats {risk_ctx} as acceptable without explanation",
            "It is solved by memorizing one fixed answer without context",
        ]
    elif difficulty_clean == 'hard':
        stem = (
            f"A {grade_clean} learner must apply {topic_title} in {subject_clean.title()} during a {task_ctx}. "
            "Which method is most rigorous?"
        )
        correct = f"Model assumptions, test alternatives, and justify the final choice using {evidence_ctx}"
        distractors = [
            "Pick the first plausible method and skip verification",
            f"Use a solution from another topic and ignore {risk_ctx}",
            "Ignore constraints and optimize only for speed",
        ]
    elif difficulty_clean == 'expert':
        stem = (
            f"For advanced work in {subject_clean.title()} on {topic_title}, which practice improves reliability most?"
        )
        correct = f"Perform iterative validation, error analysis, and metric-based refinement against {evidence_ctx}"
        distractors = [
            "Freeze the first output and avoid reevaluation",
            "Optimize presentation while ignoring model performance",
            f"Skip documentation and leave {risk_ctx} untracked",
        ]
    else:
        stem = (
            f"When studying {topic_title} in {subject_clean.title()} ({grade_clean}), what is the best next step in this {task_ctx}?"
        )
        correct = f"Break the problem into parts and connect each step to {subject_focus} using {evidence_ctx}"
        distractors = [
            "Guess quickly and move on without checking reasoning",
            f"Use one memorized rule and ignore {risk_ctx}",
            "Avoid evidence and rely entirely on intuition",
        ]

    options = [correct] + distractors
    shift = variant_index % len(options)
    options = options[shift:] + options[:shift]
    correct_index = options.index(correct)

    return {
        'text': stem,
        'options': options,
        'correct_index': correct_index,
        'topic': topic_clean,
        'hint': f"Focus on the main principle of {topic_title} before choosing an answer.",
        'explanation': f"The correct option applies disciplined reasoning to {topic_title} within {subject_clean.title()}.",
    }


def save_generated_questions(questions: List[Dict[str, Any]], subject: str, grade: str, difficulty: str, generated_by: int = None, seed: int | None = None) -> List[Question]:
    """Save generated questions to the database. Includes seed in metadata when provided."""
    saved_questions = []
    
    for question_data in questions:
        question = Question(
            subject=subject,
            grade=grade,
            difficulty=difficulty,
            text=question_data['text'],
            options=question_data['options'],
            correct_index=question_data['correct_index'],
            hint=question_data['hint'],
            explanation=question_data['explanation'],
            syllabus_topic=question_data['topic'],
            is_generated=True,
            generated_by=generated_by,
            generation_meta={
                'generated_at': datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                'generator_version': '1.0',
                'seed': seed
            }
        )
        
        db.session.add(question)
        saved_questions.append(question)
    
    try:
        db.session.commit()
        return saved_questions
    except Exception as e:
        db.session.rollback()
        raise e


def get_or_generate_questions(subject: str, grade: str, difficulty: str, count: int = 10, generated_by: int = None) -> List[Question]:
    """Get existing questions or generate new ones if needed."""
    # Try to get existing questions first
    existing_questions = Question.query.filter(
        Question.subject == subject,
        Question.grade == grade,
        Question.difficulty == difficulty
    ).limit(count).all()
    
    if len(existing_questions) >= count:
        return existing_questions[:count]
    
    # Generate additional questions if needed
    needed = count - len(existing_questions)
    generated_data = generate_stem_questions(subject, grade, difficulty, needed)
    
    # Save generated questions
    new_questions = save_generated_questions(generated_data, subject, grade, difficulty, generated_by)
    
    return existing_questions + new_questions
