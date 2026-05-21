import os
import sys
import time
import json
import random
import math
import threading
import pandas as pd
import pymongo
from flask import Flask, request, jsonify, render_template_string
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# ==========================================
# 1. कॉन्फ़िगरेशन और क्रेडेंशियल्स
# ==========================================
BOT_TOKEN = "8198941867:AAGm52kXRaXX8AINjsRoVnlHSyXfilSjGm4"
GROUP_ID = -1003687531473
MONGO_URI = "mongodb+srv://mailforfulltest_db_user:1vmiEQA28y0ok4Fh@cluster0.k85vzmp.mongodb.net/?appName=Cluster0"
SHEET_ID = "1cPPxwPTgDHfKAwLc_7ZG9WsAMUhYsiZrbJhfV0gN6W4"

# Render डोमेन को ऑटो-डिटेक्ट करने के लिए एनवायरनमेंट वेरिएबल
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")

# इनिशियलाइजेशन
bot = TeleBot(BOT_TOKEN)
db_client = pymongo.MongoClient(MONGO_URI)
db = db_client["AgriFullTestDB"]

# MongoDB कलेक्शंस
questions_col = db["questions"]
tests_col = db["generated_tests"]
results_col = db["user_results"]

app = Flask(__name__)

# ==========================================
# 2. गूगल शीट डेटा सिंकिंग लॉजिक
# ==========================================
def sync_data_from_sheet():
    """गूगल शीट से सीधे डेटा डाउनलोड कर MongoDB में स्ट्रक्चर्ड फॉर्मेट में सेव करना"""
    csv_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
    try:
        df = pd.read_csv(csv_url)
        df.columns = [str(c).strip() for c in df.columns]
        
        questions_col.delete_many({}) # पुराना पुराना साफ़ करना
        
        loaded_questions = []
        for _, row in df.iterrows():
            topic = str(row.get('Topic Name', row.get('topic', ''))).strip()
            q_text = str(row.get('Question', row.get('question', ''))).strip()
            
            opts = [
                str(row.get('A', '')).strip(),
                str(row.get('B', '')).strip(),
                str(row.get('C', '')).strip(),
                str(row.get('D', '')).strip(),
                str(row.get('E', '')).strip()
            ]
            
            ans_raw = str(row.get('Answer', row.get('answer', ''))).strip().upper()
            explanation = str(row.get('Explanation', row.get('explanation', ''))).strip()
            
            if not q_text or len(opts) < 5:
                continue
                
            # सही ऑप्शन का इंडेक्स निकालना (A->0, B->1, etc.)
            ans_map = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}
            correct_idx = ans_map.get(ans_raw, 0)
            
            # स्ट्रक्चर्ड डेटा फॉर्मेट
            q_doc = {
                "topic": topic if topic else "General Agriculture",
                "question": q_text,
                "options": opts,
                "correct_index": correct_idx,
                "explanation": explanation
            }
            loaded_questions.append(q_doc)
            
        if loaded_questions:
            questions_col.insert_many(loaded_questions)
            print(f"Successfully synced {len(loaded_questions)} questions from Sheet to Mongo.")
            return True
    except Exception as e:
        print(f"Error syncing sheet: {str(e)}")
    return False

# ==========================================
# 3. टेस्ट जनरेशन और टेलीग्राम पब्लिशिंग इंजन
# ==========================================
def generate_and_publish_all():
    """सारे सब्जेक्ट-वाइज और फिर 85 फुल मेंस टेस्ट ग्रुप में सीक्वेंस से भेजना"""
    print("🚀 टेस्ट सीरीज जनरेशन और पब्लिशिंग इंजन शुरू हो रहा है...")
    
    # डेटा री-सिंक करना
    sync_data_from_sheet()
    all_qs = list(questions_col.find({}))
    if not all_qs:
        print("❌ डेटाबेस में कोई प्रश्न नहीं मिला। प्रोसेस कैंसल।")
        return
        
    tests_col.delete_many({}) # रिफ्रेश टेस्ट्स
    
    # --- पार्ट 1: सब्जेक्ट वाइज टेस्ट जनरेशन ---
    subjects = {}
    for q in all_qs:
        sub = q['topic']
        if sub not in subjects:
            subjects[sub] = []
        subjects[sub].append(q)
        
    for sub_name, q_list in subjects.items():
        # बोल्ड में सब्जेक्ट टैग भेजना
        bot.send_message(GROUP_ID, f"🌟 <b>{sub_name.upper()}</b> 🌟\n\n📌 <i>सब्जेक्ट वाइज टेस्ट सीरीज शुरू।</i>", parse_mode="HTML")
        time.sleep(2)
        
        chunk_size = 25
        total_sets = math.ceil(len(q_list) / chunk_size)
        
        for i in range(total_sets):
            chunk = q_list[i*chunk_size : (i+1)*chunk_size]
            test_id = f"{sub_name.replace(' ', '_')}_Test_{i+1}"
            test_title = f"{sub_name} Test {i+1}"
            
            # टेस्ट को ट्रैक करने के लिए डेटाबेस में सेव करना
            tests_col.insert_one({
                "test_id": test_id,
                "title": test_title,
                "type": "subject",
                "questions": chunk
            })
            
            # डायरेक्ट टेलीग्राम नेटिव क्विज भेजना
            bot.send_message(GROUP_ID, f"📝 <b>{test_title}</b> शुरू हो रहा है...", parse_mode="HTML")
            time.sleep(1)
            
            for idx, q in enumerate(chunk):
                try:
                    bot.send_poll(
                        chat_id=GROUP_ID,
                        question=f"Q{idx+1}: {q['question']}",
                        options=q['options'],
                        type="quiz",
                        correct_option_id=q['correct_index'],
                        is_anonymous=False
                    )
                    time.sleep(1.5) # टेलीग्राम फ्लडिंग लिमिट से बचने के लिए
                except Exception as ex:
                    print(f"Poll Error: {str(ex)}")
            
            # ठीक नीचे Multiattempt HTML लिंक सेंड करना
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text="🚀 Open HTML Quiz", web_app=WebAppInfo(url=f"{RENDER_URL}/test/{test_id}")))
            bot.send_message(GROUP_ID, f"🔗 <b>{test_title} Multiattempt</b>\n\nऊपर दिए गए टेस्ट को कस्टमाइज़्ड टाइमर और री-अटेम्प्ट विकल्पों के साथ ब्राउज़र पर देने के लिए नीचे क्लिक करें।", reply_markup=markup, parse_mode="HTML")
            time.sleep(3)

    # --- पार्ट 2: एएफओ मेंस फुल लेंथ टेस्ट्स (85 सेट्स) ---
    bot.send_message(GROUP_ID, "🏆 <b>AFO MAINS FULL LENGTH TESTS</b> 🏆\n\n📌 <i>मिक्स्ड सब्जेक्ट्स के 85 फुल मॉक टेस्ट सीरीज शुरू।</i>", parse_mode="HTML")
    time.sleep(2)
    
    for t_idx in range(1, 86):
        # हर सेट के लिए 60 रैंडम मिक्स प्रश्न चुनना (बिना रिपेटीशन के या रैंडम सैंपल)
        # बेहतर डिस्ट्रीब्यूशन के लिए पूरे पूल से 60 प्रश्न सैंपल करेंगे
        sampled_qs = random.sample(all_qs, min(60, len(all_qs)))
        test_id = f"AFO_Mains_Test_{t_idx}"
        test_title = f"AFO Mains Test {t_idx}"
        
        tests_col.insert_one({
            "test_id": test_id,
            "title": test_title,
            "type": "mains",
            "questions": sampled_qs
        })
        
        bot.send_message(GROUP_ID, f"🔥 <b>{test_title}</b> (60 Questions) शुरू हो रहा है...", parse_mode="HTML")
        time.sleep(1)
        
        # टेलीग्राम में 60 क्विज भेजना
        for idx, q in enumerate(sampled_qs):
            try:
                bot.send_poll(
                    chat_id=GROUP_ID,
                    question=f"Q{idx+1}: {q['question']}",
                    options=q['options'],
                    type="quiz",
                    correct_option_id=q['correct_index'],
                    is_anonymous=False
                )
                time.sleep(1.5)
            except Exception as ex:
                print(f"Mains Poll Error: {str(ex)}")
                
        # ठीक नीचे Reattempt HTML लिंक सेंड करना (45 मिनट फिक्स टाइमर वाला)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text="⏳ Open AFO Full Test App", web_app=WebAppInfo(url=f"{RENDER_URL}/test/{test_id}")))
        bot.send_message(GROUP_ID, f"🔗 <b>{test_title} Reattempt</b>\n\nइस 60 प्रश्नों के मेंस टेस्ट को 45 मिनट के लाइव टाइमर और 1/4 नेगेटिव मार्किंग के साथ देने के लिए नीचे क्लिक करें।", reply_markup=markup, parse_mode="HTML")
        time.sleep(5)
        
    print("✅ सभी टेस्ट सफलतापूर्वक टेलीग्राम पर पब्लिश कर दिए गए हैं!")

# ==========================================
# 4. FLASK WEB APP ROUTES & INTERFACE
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ test_title }}</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f4f6f9; margin: 0; padding: 15px; color: #333; }
        .card { background: #fff; padding: 20px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); margin-bottom: 15px; max-width: 600px; margin-left: auto; margin-right: auto; }
        .header { text-align: center; font-size: 22px; font-weight: bold; color: #2c3e50; border-bottom: 2px solid #eaeaea; padding-bottom: 10px; margin-bottom: 15px; }
        .footer { text-align: center; font-size: 15px; font-weight: bold; color: #7f8c8d; border-top: 2px solid #eaeaea; padding-top: 10px; margin-top: 20px; }
        .btn { background: #3498db; color: #fff; border: none; padding: 12px 20px; font-size: 16px; border-radius: 8px; cursor: pointer; width: 100%; margin-top: 10px; font-weight: bold; }
        .btn:hover { background: #2980b9; }
        .option-btn { background: #fff; border: 2px solid #dcdde1; text-align: left; padding: 12px; font-size: 15px; border-radius: 8px; margin-top: 8px; cursor: pointer; width: 100%; transition: all 0.2s; display: block; }
        .option-btn.selected { border-color: #3498db; background-color: #ebf5fb; }
        .option-btn.correct { border-color: #2ecc71; background-color: #e8f8f5; }
        .option-btn.wrong { border-color: #e74c3c; background-color: #fdeadc; }
        #timer-box { font-size: 18px; font-weight: bold; color: #e74c3c; text-align: center; margin-bottom: 15px; }
        .hidden { display: none; }
        select { width: 100%; padding: 10px; font-size: 16px; border-radius: 8px; margin-bottom: 15px; border: 1px solid #ccc; }
        .explanation-box { background: #f9f9f9; padding: 12px; border-left: 4px solid #3498db; margin-top: 10px; border-radius: 4px; font-size: 14px; }
    </style>
</head>
<body>

    <div class="card">
        <div class="header">By Satyam Sir</div>
        <div id="test-title-ui" style="text-align:center; font-weight:bold; margin-bottom:10px;">{{ test_title }}</div>
        
        <div id="secure-screen" class="hidden">
            <h3 style="color:#e74c3c; text-align:center;">🚫 Access Denied</h3>
            <p style="text-align:center;">यह टेस्ट केवल अधिकृत टेलीग्राम प्राइवेट ग्रुप के अंदर ही लाइव खोला जा सकता है। इसे बाहर शेयर करना वर्जित है।</p>
        </div>

        <div id="setup-screen">
            <label><b>Choose Language / भाषा चुनें:</b></label>
            <select id="lang-select">
                <option value="EN">English</option>
                <option value="HI">Hindi (Professional Agriculture Terminology)</option>
            </select>

            {% if test_type == "subject" %}
            <label><b>Select Timer per Question:</b></label>
            <select id="timer-select">
                <option value="10">10 Seconds</option>
                <option value="15">15 Seconds</option>
                <option value="20">20 Seconds</option>
                <option value="25">25 Seconds</option>
            </select>
            {% else %}
            <p><b>⏱️ Time Allotted:</b> 45 Minutes (Fixed for AFO Mains Mock)</p>
            <p><b>⚠️ Marking Scheme:</b> Correct: +1 | Wrong: -0.25 (1/4 Negative)</p>
            {% endif %}

            <button class="btn" onclick="startTestEngine()">Start Test</button>
        </div>

        <div id="quiz-screen" class="hidden">
            <div id="timer-box">Time Left: <span id="timer-disp">00:00</span></div>
            <div id="progress" style="font-weight:bold; margin-bottom:10px;"></div>
            <div id="question-text" style="font-size:17px; font-weight:600; margin-bottom:15px;"></div>
            <div id="options-box"></div>
            <button class="btn" id="next-btn" onclick="nextQuestion()">Next Question</button>
        </div>

        <div id="result-screen" class="hidden">
            <h3 style="text-align:center; color:#2ecc71;">📊 Test Completed!</h3>
            <div id="score-matrix" style="font-size:16px; line-height:1.6; margin-bottom:15px;"></div>
            <button class="btn" style="background:#2ecc71;" onclick="viewReviewSection()">Review Answers / Check Explanations</button>
            <button class="btn" style="background:#95a5a6; margin-top:5px;" onclick="location.reload()">🔄 Re-attempt Test</button>
        </div>
        
        <div id="review-screen" class="hidden">
            <h3 style="text-align:center;">📝 Answer Review</h3>
            <div id="review-container"></div>
            <button class="btn" onclick="location.reload()">🔄 Back to Main / Re-attempt</button>
        </div>

        <div class="footer">Agri Learning Point</div>
    </div>

    <script>
        let tg = window.Telegram.WebApp;
        tg.expand();

        // सिक्योरिटी वेरिफिकेशन: टेलीग्राम एनवायरनमेंट चेक
        if (!tg.initDataUnsafe || !tg.initDataUnsafe.user) {
            document.getElementById("setup-screen").classList.add("hidden");
            document.getElementById("secure-screen").classList.remove("hidden");
        }

        // सर्वर से रेंडर किया गया ओरिजिनल डेटा पूल
        const rawQuestions = {{ questions_json | safe }};
        const testType = "{{ test_type }}";
        
        let shuffledQuestions = [];
        let currentIdx = 0;
        let selectedAnswers = {}; // qIdx: selectedOptText
        let totalTimeInSeconds = 0;
        let countdownInterval;
        let currentLang = "EN";

        // एग्रीकल्चर शब्दावली डिक्शनरी (रोबोटिक ट्रांसलेशन से बचने के लिए कस्टम मैप)
        const agGlossary = {
            "Agronomy": "कृषि विज्ञान (शस्य विज्ञान)",
            "Horticulture": "बागवानी (उद्यान विज्ञान)",
            "Soil Science": "मृदा विज्ञान",
            "Animal Husbandry": "पशुपालन एवं डेयरी विज्ञान",
            "Fisheries": "मत्स्य पालन"
        };

        function shuffleArray(array) {
            for (let i = array.length - 1; i > 0; i--) {
                const j = Math.floor(Math.random() * (i + 1));
                [array[i], array[j]] = [array[j], array[i]];
            }
        }

        function startTestEngine() {
            currentLang = document.getElementById("lang-select").value;
            document.getElementById("setup-screen").classList.add("hidden");
            document.getElementById("quiz-screen").classList.remove("hidden");

            // गहरा कॉपी बनाना ताकि ओरिजिनल डेटा डिस्टर्ब न हो और शफलिंग सेफ रहे
            shuffledQuestions = JSON.parse(JSON.stringify(rawQuestions));
            
            // 1. प्रश्नों को शफल करना
            shuffleArray(shuffledQuestions);

            // 2. हर प्रश्न के अंदर ऑप्शंस को सुरक्षित रूप से शफल करना (करेक्ट आंसर फ्लैग को टैग रखकर)
            shuffledQuestions.forEach(q => {
                let optsWithFlags = q.options.map((opt, i) => {
                    return { text: opt, isCorrect: (i === q.correct_index) };
                });
                shuffleArray(optsWithFlags);
                q.shuffledOptions = optsWithFlags;
            });

            // 3. टाइमर कस्टमाइज़ेशन सेट करना
            if (testType === "subject") {
                let secPerQ = parseInt(document.getElementById("timer-select").value);
                totalTimeInSeconds = shuffledQuestions.length * secPerQ;
            } else {
                totalTimeInSeconds = 45 * 60; // फिक्स 45 मिनट
            }

            startTimerEngine();
            renderQuestion();
        }

        function startTimerEngine() {
            countdownInterval = setInterval(() => {
                let mins = Math.floor(totalTimeInSeconds / 60);
                let secs = totalTimeInSeconds % 60;
                document.getElementById("timer-disp").innerText = 
                    (mins < 10 ? "0" : "") + mins + ":" + (secs < 10 ? "0" : "") + secs;
                
                if (totalTimeInSeconds <= 300) {
                    document.getElementById("timer-box").style.color = "#f39c12"; // आखिरी 5 मिनट पीला
                }
                if (totalTimeInSeconds <= 60) {
                    document.getElementById("timer-box").style.color = "#e74c3c"; // आखिरी 1 मिनट लाल
                }

                if (totalTimeInSeconds <= 0) {
                    clearInterval(countdownInterval);
                    autoSubmitTest();
                }
                totalTimeInSeconds--;
            }, 1000);
        }

        function renderQuestion() {
            let q = shuffledQuestions[currentIdx];
            document.getElementById("progress").innerText = `Question ${currentIdx + 1} of ${shuffledQuestions.length}`;
            document.getElementById("question-text").innerText = q.question;

            let optContainer = document.getElementById("options-box");
            optContainer.innerHTML = "";

            q.shuffledOptions.forEach((opt, idx) => {
                let btn = document.createElement("button");
                btn.className = "option-btn";
                btn.innerText = opt.text;
                if (selectedAnswers[currentIdx] === opt.text) {
                    btn.classList.add("selected");
                }
                btn.onclick = () => {
                    let allBtns = optContainer.getElementsByClassName("option-btn");
                    for (let b of allBtns) b.classList.remove("selected");
                    btn.classList.add("selected");
                    selectedAnswers[currentIdx] = opt.text;
                };
                optContainer.appendChild(btn);
            });

            if (currentIdx === shuffledQuestions.length - 1) {
                document.getElementById("next-btn").innerText = "Submit Test";
            } else {
                document.getElementById("next-btn").innerText = "Next Question";
            }
        }

        function nextQuestion() {
            if (currentIdx < shuffledQuestions.length - 1) {
                currentIdx++;
                renderQuestion();
            } else {
                clearInterval(countdownInterval);
                processResult();
            }
        }

        function autoSubmitTest() {
            alert("⏰ Time is up! Your test is being submitted automatically.");
            processResult();
        }

        let finalCorrect = 0, finalWrong = 0, finalUnattempted = 0, finalScore = 0;

        function processResult() {
            document.getElementById("quiz-screen").classList.add("hidden");
            document.getElementById("result-screen").classList.remove("hidden");

            finalCorrect = 0; finalWrong = 0; finalUnattempted = 0;

            shuffledQuestions.forEach((q, i) => {
                let selected = selectedAnswers[i];
                if (!selected) {
                    finalUnattempted++;
                } else {
                    let matchingOpt = q.shuffledOptions.find(o => o.text === selected);
                    if (matchingOpt && matchingOpt.isCorrect) {
                        finalCorrect++;
                    } else {
                        finalWrong++;
                    }
                }
            });

            // 1/4 नेगेटिव मार्किंग लॉजिक
            finalScore = (finalCorrect * 1) - (finalWrong * 0.25);

            document.getElementById("score-matrix").innerHTML = `
                📌 <b>Total Questions:</b> ${shuffledQuestions.length}<br>
                ✅ <b>Correct Answers:</b> ${finalCorrect}<br>
                ❌ <b>Wrong Answers:</b> ${finalWrong}<br>
                ⚪ <b>Unattempted:</b> ${finalUnattempted}<br><br>
                🏆 <b>Your Final Score:</b> <span style="font-size:20px; color:#3498db;">${finalScore.toFixed(2)}</span>
            `;

            // MongoDB में डेटा स्टोर करने के लिए सेंड करना
            let payload = {
                username: tg.initDataUnsafe.user.username || "Anonymous",
                first_name: tg.initDataUnsafe.user.first_name,
                user_id: tg.initDataUnsafe.user.id,
                test_title: "{{ test_title }}",
                score: finalScore
            };
            fetch("/submit-score", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
        }

        function viewReviewSection() {
            document.getElementById("result-screen").classList.add("hidden");
            document.getElementById("review-screen").classList.remove("hidden");

            let container = document.getElementById("review-container");
            container.innerHTML = "";

            shuffledQuestions.forEach((q, i) => {
                let div = document.createElement("div");
                div.style.borderBottom = "1px solid #ddd";
                div.style.paddingBottom = "15px";
                div.style.marginTop = "15px";

                let qTitle = document.createElement("p");
                qTitle.innerHTML = `<b>Q${i+1}:</b> ${q.question}`;
                div.appendChild(qTitle);

                q.shuffledOptions.forEach(opt => {
                    let oDiv = document.createElement("div");
                    oDiv.style.padding = "8px";
                    oDiv.style.margin = "4px 0";
                    oDiv.style.borderRadius = "4px";
                    oDiv.innerText = opt.text;

                    if (opt.isCorrect) {
                        oDiv.style.background = "#d4edda";
                        oDiv.style.color = "#155724";
                        oDiv.innerText += "  ✔ (Correct)";
                    } else if (selectedAnswers[i] === opt.text) {
                        oDiv.style.background = "#f8d7da";
                        oDiv.style.color = "#721c24";
                        oDiv.innerText += "  ✖ (Your Choice)";
                    } else {
                        oDiv.style.background = "#fff";
                        oDiv.style.border = "1px solid #eee";
                    }
                    div.appendChild(oDiv);
                });

                if (q.explanation) {
                    let exp = document.createElement("div");
                    exp.className = "explanation-box";
                    exp.innerHTML = `<b>Explanation:</b> ${q.explanation}`;
                    div.appendChild(exp);
                }
                container.appendChild(div);
            });
        }
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return "<h3>Agri Learning Point Core API Server Running Perfectly.</h3>"

@app.route("/test/<test_id>")
def serve_test(test_id):
    test_data = tests_col.find_one({"test_id": test_id})
    if not test_data:
        return "<h3>Error: Test Not Found!</h3>", 404
        
    return render_template_string(
        HTML_TEMPLATE,
        test_title=test_data["title"],
        test_type=test_data["type"],
        questions_json=json.dumps(test_data["questions"])
    )

@app.route("/submit-score",肌 methods=["POST"])
def submit_score():
    data = request.json
    if data:
        results_col.insert_one({
            "user_id": data.get("user_id"),
            "username": data.get("username"),
            "first_name": data.get("first_name"),
            "test_title": data.get("test_title"),
            "score": data.get("score"),
            "timestamp": time.time()
        })
        return jsonify({"status": "success"})
    return jsonify({"status": "failed"}), 400

# ==========================================
# 5. SERVER RUNNER & BACKGROUND TASKS
# ==========================================
if __name__ == "__main__":
    # अगर स्क्रिप्ट टर्मिनल से 'python app.py sync' करके चलाई जाए, तो टेलीग्राम पब्लिशिंग शुरू होगी
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        generate_and_publish_all()
    else:
        # Render वेब डिप्लॉयमेंट पोर्ट पर सर्विस स्टार्ट करना
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port)