import random

def generate_fallback_mcqs(topic: str, count: int, difficulty: str, subject: str, grade: str) -> list:
    """
    Generates highly sensible, original fallback questions if the AI service fails.
    Tailors the vocabulary and complexity based on difficulty and grade.
    """
    safe_topic = topic.strip().title() if topic else "General Concepts"
    safe_subject = subject.strip().capitalize() if subject else "STEM"
    
    mcqs = []
    
    # Define difficulty-based contextual phrasing
    if difficulty.lower() == "hard":
        action = "Critically analyze the primary function of"
        distractor_prefix = "A commonly misunderstood"
        scenario = "In an advanced application scenario,"
    elif difficulty.lower() == "medium":
        action = "Identify the core principle behind"
        distractor_prefix = "An alternative but incorrect"
        scenario = "When applying this concept in practice,"
    else:
        action = "What is the basic definition of"
        distractor_prefix = "A completely unrelated"
        scenario = "In basic terms,"

    for i in range(count):
        # Create sensible, context-aware fallback templates
        templates = [
            {
                "question": f"{scenario} how does the concept of {safe_topic} directly impact outcomes in {safe_subject}?",
                "correct_answer": "A",
                "options": [
                    f"It provides the foundational framework necessary for optimizing {safe_topic} processes.",
                    f"{distractor_prefix} theory suggests it eliminates the need for mathematical modeling.",
                    f"It relies entirely on outdated methodologies that are rarely used in modern {safe_subject}.",
                    f"It primarily acts as a theoretical placeholder without practical application."
                ],
                "explanation": f"In {safe_subject}, {safe_topic} serves as a foundational framework, allowing practitioners to optimize and predict reliable outcomes."
            },
            {
                "question": f"{action} {safe_topic} within a {grade.lower()}-level educational context?",
                "correct_answer": "B",
                "options": [
                    f"It is exclusively used for visual design and has no technical merit.",
                    f"It acts as a key mechanism to solve complex problems relevant to {safe_subject}.",
                    f"It is a deprecated practice that has been replaced by manual computation.",
                    f"It is only applicable in non-scientific disciplines."
                ],
                "explanation": f"{safe_topic} is highly relevant in {safe_subject} as a mechanism for solving complex, domain-specific problems effectively."
            },
            {
                "question": f"Which of the following best describes a critical limitation or boundary condition of {safe_topic}?",
                "correct_answer": "C",
                "options": [
                    f"It can solve any problem instantaneously regardless of the inputs provided.",
                    f"It requires zero computational or physical resources to execute perfectly.",
                    f"Its effectiveness is heavily dependent on the quality of initial parameters and contextual constraints.",
                    f"It operates completely independently of the laws governing {safe_subject}."
                ],
                "explanation": f"Like all technical concepts in {safe_subject}, {safe_topic} is constrained by the quality of inputs and the environment in which it operates."
            }
        ]
        
        # Pick a template and randomize the options layout securely
        template = templates[i % len(templates)]
        
        # Shuffle options while keeping track of the correct answer
        correct_text = template["options"][0 if template["correct_answer"] == "A" else 
                                          1 if template["correct_answer"] == "B" else 
                                          2] # Based on the hardcoded correct answers above
        
        shuffled_options = template["options"][:]
        random.shuffle(shuffled_options)
        new_correct_index = shuffled_options.index(correct_text)
        new_correct_letter = ["A", "B", "C", "D"][new_correct_index]

        mcqs.append({
            "text": template["question"],           # CHANGED from "question"
            "options": shuffled_options,
            "correct_index": new_correct_index,     # CHANGED from "correct_answer"
            "explanation": template["explanation"],
            "difficulty": difficulty,
            "topic": safe_topic,
            "source": "robust_fallback"
        })
        
    return mcqs