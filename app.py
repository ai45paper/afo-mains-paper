import os
import sys
import time
import json
import random
import math
import urllib.request
import csv
import io
import threading
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

RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://afo-mains-paper.onrender.com")

# इनिशियलाइजेशन
bot = TeleBot(BOT_TOKEN)
db_client = pymongo.MongoClient(MONGO_URI)
db = db_client["AgriFullTestDB"]

questions_col = db["questions"]
tests_col = db["generated_tests"]
results_col = db["user_results"]

app = Flask(__name__)

# ==========================================
# 2. गूगल शीट डेटा सिंकिंग लॉजिक (ULTRA ROBUST MODE)
# ==========================================
def sync_data_from_sheet():
    # Cache को तोड़ने के लिए लिंक के अंत में रैंडम टाइमस्टैम्प जोड़ा गया है
    csv_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&t={time.time()}"
    try:
        response = urllib.request.urlopen(csv_url)
        csv_data = response.read().decode('utf-8')
        
        # अब हम नामों (Headers) की जगह सीधे कॉलम नंबर (Index) से पढ़ेंगे
        reader = csv.reader(io.StringIO(csv_data))
        rows = list(reader)
        
        if len(rows) <= 1:
            print("❌ Sheet is completely empty or has only headers.")
            return False
            
        questions_col.delete_many({}) 
        
        loaded_questions = []
        for index, row in enumerate(rows[1:]): # पहली लाइन (Headers) को इग्नोर करना
            if len(row) < 8: # अगर रो में पर्याप्त कॉलम नहीं हैं, तो छोड़ दें
                continue
                
            topic = row[0].strip() if row[0].strip() else "General Agriculture"
            q_text = row[1].strip()
            
            # कॉलम 2 से 6 तक ऑप्शंस हैं (A, B, C, D, E)
            opts = [
                row[2].strip() if len(row) > 2 else '',
                row[3].strip() if len(row) > 3 else '',
                row[4].strip() if len(row) > 4 else '',
                row[5].strip() if len(row) > 5 else '',
                row[6].strip() if len(row) > 6 else ''
            ]
            
            ans_raw = row[7].strip() if len(row) > 7 else ''
            explanation = row[8].strip() if len(row) > 8 else ''
            
            # अगर प्रश्न खाली है या 5 ऑप्शंस नहीं हैं, तो स्किप करें
            if not q_text or len([o for o in opts if o]) < 5:
                continue
            
            # 🎯 स्मार्ट आंसर मैचिंग लॉजिक
            correct_idx = 0
            if ans_raw.upper() in ['A', 'B', 'C', 'D', 'E']:
                # अगर आंसर A, B, C है
                correct_idx = ord(ans_raw.upper()) - 65
            else:
                # अगर आंसर में पूरा टेक्स्ट (जैसे 'Dee-gee-woo') लिखा है
                for i, opt in enumerate(opts):
                    if ans_raw.lower() == opt.lower():
                        correct_idx = i
                        break
            
            q_doc = {
                "topic": topic,
                "question": q_text,
                "options": opts,
                "correct_index": correct_idx,
                "explanation": explanation
            }
            loaded_questions.append(q_doc)
            
        if loaded_questions:
            questions_col.insert_many(loaded_questions)
            print(f"✅ Successfully synced {len(loaded_questions)} questions from Sheet to Mongo!")
            return True
        else:
            print("❌ No valid questions found. All were skipped.")
    except Exception as e:
        print(f"❌ Error syncing sheet: {str(e)}")
    return False

# ==========================================
# 3. टेस्ट जनरेशन और टेलीग्राम पब्लिशिंग इंजन (Background Worker)
# ==========================================
def generate_and_publish_all():
    print("🚀 टेस्ट सीरीज जनरेशन और पब्लिशिंग इंजन शुरू हो रहा है...")
    sync_data_from_sheet()
    all_qs = list(questions_col.find({}))
    if not all_qs:
        print("❌ डेटाबेस में कोई प्रश्न नहीं मिला। प्रोसेस कैंसल। (कृपया चेक करें कि आपकी गूगल शीट Public है या नहीं)")
        return
        
    tests_col.delete_many({}) 

    # 🛡️ Telegram Anti-Flood Wrapper (Error 429 Solver)
    def safe_request(func, *args, **kwargs):
        while True:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e).lower()
                # अगर Error 429 आता है, तो बॉट क्रैश नहीं होगा बल्कि इंतज़ार करेगा
                if "429" in error_str or "retry after" in error_str:
                    wait_time = 20 # डिफ़ॉल्ट
                    try:
                        # टेलीग्राम जितने सेकंड रुकने को कहेगा, बॉट उतने ही सेकंड निकालेगा
                        wait_time = int(error_str.split("retry after ")[1].split()[0])
                    except:
                        pass
                    print(f"⏳ Telegram Speed Limit Hit! Waiting for {wait_time} seconds to resume...")
                    time.sleep(wait_time + 2) # टेलीग्राम के समय से 2 सेकंड ज़्यादा का बफर
                else:
                    print(f"⚠️ Action Error: {e}")
                    return None

    # 📌 मैसेज पिन करने के लिए सुरक्षित फंक्शन
    def safe_pin(msg_id):
        if msg_id:
            safe_request(bot.pin_chat_message, chat_id=GROUP_ID, message_id=msg_id, disable_notification=True)

    # --- पार्ट 1: सब्जेक्ट वाइज टेस्ट ---
    subjects = {}
    for q in all_qs:
        sub = q['topic']
        if sub not in subjects:
            subjects[sub] = []
        subjects[sub].append(q)
        
    for sub_name, q_list in subjects.items():
        # 1. सब्जेक्ट का नाम और पिन
        msg_sub = safe_request(bot.send_message, chat_id=GROUP_ID, text=f"🌟 <b>{sub_name.upper()}</b> 🌟", parse_mode="HTML")
        if msg_sub: safe_pin(msg_sub.message_id)
        time.sleep(2)
        
        chunk_size = 25
        total_sets = math.ceil(len(q_list) / chunk_size)
        
        for i in range(total_sets):
            chunk = q_list[i*chunk_size : (i+1)*chunk_size]
            test_id = f"{sub_name.replace(' ', '_')}_Test_{i+1}"
            test_title = f"{sub_name} Test {i+1}"
            
            tests_col.insert_one({
                "test_id": test_id, "title": test_title, "type": "subject", "questions": chunk
            })
            
            # 2. टेस्ट का नाम और पिन
            msg_title = safe_request(bot.send_message, chat_id=GROUP_ID, text=f"📝 <b>{test_title}</b>", parse_mode="HTML")
            if msg_title: safe_pin(msg_title.message_id)
            time.sleep(1)
            
            # 3. क्विज सेंड करना
            for idx, q in enumerate(chunk):
                safe_request(
                    bot.send_poll, chat_id=GROUP_ID, question=f"Q{idx+1}: {q['question']}", options=q['options'],
                    type="quiz", correct_option_id=q['correct_index'], is_anonymous=False
                )
                time.sleep(2.5) # स्पैम से बचने के लिए समय 2.5 सेकंड कर दिया गया है
            
            # 4. री-अटेम्प्ट मोड का मैसेज और पिन
            msg_reattempt = safe_request(bot.send_message, chat_id=GROUP_ID, text=f"🔗 <b>{test_title} (Reattempt Mode)</b>", parse_mode="HTML")
            if msg_reattempt: safe_pin(msg_reattempt.message_id)
            time.sleep(1)
            
            # 5. HTML लिंक सेंड करना
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text="🚀 Open HTML Quiz", url=f"{RENDER_URL}/test/{test_id}"))
            safe_request(bot.send_message, chat_id=GROUP_ID, text=f"👆 ऊपर दिए गए टेस्ट को कस्टमाइज़्ड टाइमर के साथ देने के लिए नीचे क्लिक करें।", reply_markup=markup, parse_mode="HTML")
            time.sleep(4)

    # --- पार्ट 2: एएफओ मेंस फुल लेंथ टेस्ट्स (85 सेट्स) ---
    msg_mains = safe_request(bot.send_message, chat_id=GROUP_ID, text="🏆 <b>AFO MAINS FULL LENGTH TESTS</b> 🏆", parse_mode="HTML")
    if msg_mains: safe_pin(msg_mains.message_id)
    time.sleep(3)
    
    for t_idx in range(1, 86):
        sampled_qs = random.sample(all_qs, min(60, len(all_qs)))
        test_id = f"AFO_Mains_Test_{t_idx}"
        test_title = f"AFO Mains Test {t_idx}"
        
        tests_col.insert_one({
            "test_id": test_id, "title": test_title, "type": "mains", "questions": sampled_qs
        })
        
        msg_mains_title = safe_request(bot.send_message, chat_id=GROUP_ID, text=f"🔥 <b>{test_title}</b> (60 Questions)", parse_mode="HTML")
        if msg_mains_title: safe_pin(msg_mains_title.message_id)
        time.sleep(1)
        
        for idx, q in enumerate(sampled_qs):
            safe_request(
                bot.send_poll, chat_id=GROUP_ID, question=f"Q{idx+1}: {q['question']}", options=q['options'],
                type="quiz", correct_option_id=q['correct_index'], is_anonymous=False
            )
            time.sleep(2.5)
                
        msg_mains_reattempt = safe_request(bot.send_message, chat_id=GROUP_ID, text=f"🔗 <b>{test_title} (Reattempt Mode)</b>", parse_mode="HTML")
        if msg_mains_reattempt: safe_pin(msg_mains_reattempt.message_id)
        time.sleep(1)
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text="⏳ Open AFO Full Test App", url=f"{RENDER_URL}/test/{test_id}"))
        safe_request(bot.send_message, chat_id=GROUP_ID, text=f"👆 इस मेंस टेस्ट को 45 मिनट के टाइमर और 1/4 नेगेटिव मार्किंग के साथ देने के लिए नीचे क्लिक करें।", reply_markup=markup, parse_mode="HTML")
        time.sleep(5)
        
    # ======== अंतिम संदेश ========
    final_msg = (
        "✅ <b>SYNC PROCESS COMPLETED</b> ✅\n\n"
        "🎉 सभी टेस्ट सफलतापूर्वक ग्रुप में पब्लिश कर दिए गए हैं।\n"
        "🛑 <i>बैकग्राउंड पब्लिशिंग इंजन अब अपने आप बंद हो गया है। छात्रों के टेस्ट लिंक्स 24/7 एक्टिव रहेंगे।</i>"
    )
    safe_request(bot.send_message, chat_id=GROUP_ID, text=final_msg, parse_mode="HTML")
    print("✅ सारा काम खत्म! बैकग्राउंड थ्रेड सफलतापूर्वक बंद हो गया है।")
# ==========================================
# 4. FLASK WEB APP ROUTES 
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
        .option-btn { background: #fff; border: 2px solid #dcdde1; text-align: left; padding: 12px; font-size: 15px; border-radius: 8px; margin-top: 8px; cursor: pointer; width: 100%; display: block; }
        .option-btn.selected { border-color: #3498db; background-color: #ebf5fb; }
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
            <p style="text-align:center;">यह टेस्ट केवल अधिकृत टेलीग्राम प्राइवेट ग्रुप के अंदर ही लाइव खोला जा सकता है।</p>
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
            <p><b>⏱️ Time Allotted:</b> 45 Minutes</p>
            <p><b>⚠️ Marking Scheme:</b> Correct: +1 | Wrong: -0.25</p>
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
            <button class="btn" style="background:#2ecc71;" onclick="viewReviewSection()">Review Answers</button>
            <button class="btn" style="background:#95a5a6; margin-top:5px;" onclick="location.reload()">🔄 Re-attempt</button>
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

        if (!tg.initDataUnsafe || !tg.initDataUnsafe.user) {
            document.getElementById("setup-screen").classList.add("hidden");
            document.getElementById("secure-screen").classList.remove("hidden");
        }

        const rawQuestions = {{ questions_json | safe }};
        const testType = "{{ test_type }}";
        
        let shuffledQuestions = [];
        let currentIdx = 0;
        let selectedAnswers = {}; 
        let totalTimeInSeconds = 0;
        let countdownInterval;

        function shuffleArray(array) {
            for (let i = array.length - 1; i > 0; i--) {
                const j = Math.floor(Math.random() * (i + 1));
                [array[i], array[j]] = [array[j], array[i]];
            }
        }

        function startTestEngine() {
            document.getElementById("setup-screen").classList.add("hidden");
            document.getElementById("quiz-screen").classList.remove("hidden");

            shuffledQuestions = JSON.parse(JSON.stringify(rawQuestions));
            shuffleArray(shuffledQuestions);

            shuffledQuestions.forEach(q => {
                let optsWithFlags = q.options.map((opt, i) => {
                    return { text: opt, isCorrect: (i === q.correct_index) };
                });
                shuffleArray(optsWithFlags);
                q.shuffledOptions = optsWithFlags;
            });

            if (testType === "subject") {
                let secPerQ = parseInt(document.getElementById("timer-select").value);
                totalTimeInSeconds = shuffledQuestions.length * secPerQ;
            } else {
                totalTimeInSeconds = 45 * 60; 
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
                if (totalTimeInSeconds <= 0) {
                    clearInterval(countdownInterval);
                    processResult();
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
                if (selectedAnswers[currentIdx] === opt.text) btn.classList.add("selected");
                btn.onclick = () => {
                    let allBtns = optContainer.getElementsByClassName("option-btn");
                    for (let b of allBtns) b.classList.remove("selected");
                    btn.classList.add("selected");
                    selectedAnswers[currentIdx] = opt.text;
                };
                optContainer.appendChild(btn);
            });

            document.getElementById("next-btn").innerText = (currentIdx === shuffledQuestions.length - 1) ? "Submit Test" : "Next Question";
        }

        function nextQuestion() {
            if (currentIdx < shuffledQuestions.length - 1) {
                currentIdx++; renderQuestion();
            } else {
                clearInterval(countdownInterval); processResult();
            }
        }

        function processResult() {
            document.getElementById("quiz-screen").classList.add("hidden");
            document.getElementById("result-screen").classList.remove("hidden");

            let finalCorrect = 0, finalWrong = 0, finalUnattempted = 0;
            shuffledQuestions.forEach((q, i) => {
                let selected = selectedAnswers[i];
                if (!selected) finalUnattempted++;
                else {
                    let matchingOpt = q.shuffledOptions.find(o => o.text === selected);
                    if (matchingOpt && matchingOpt.isCorrect) finalCorrect++;
                    else finalWrong++;
                }
            });

            let finalScore = (finalCorrect * 1) - (finalWrong * 0.25);
            document.getElementById("score-matrix").innerHTML = `
                📌 <b>Total:</b> ${shuffledQuestions.length} | ✅ <b>Correct:</b> ${finalCorrect}<br>
                ❌ <b>Wrong:</b> ${finalWrong} | ⚪ <b>Unattempted:</b> ${finalUnattempted}<br><br>
                🏆 <b>Score:</b> <span style="font-size:20px; color:#3498db;">${finalScore.toFixed(2)}</span>
            `;

            let payload = {
                username: tg.initDataUnsafe.user.username || "Anonymous",
                first_name: tg.initDataUnsafe.user.first_name,
                user_id: tg.initDataUnsafe.user.id,
                test_title: "{{ test_title }}",
                score: finalScore
            };
            fetch("/submit-score", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        }

        function viewReviewSection() {
            document.getElementById("result-screen").classList.add("hidden");
            document.getElementById("review-screen").classList.remove("hidden");

            let container = document.getElementById("review-container");
            container.innerHTML = "";

            shuffledQuestions.forEach((q, i) => {
                let div = document.createElement("div");
                div.style.borderBottom = "1px solid #ddd";
                div.style.paddingBottom = "15px"; div.style.marginTop = "15px";

                let qTitle = document.createElement("p");
                qTitle.innerHTML = `<b>Q${i+1}:</b> ${q.question}`;
                div.appendChild(qTitle);

                q.shuffledOptions.forEach(opt => {
                    let oDiv = document.createElement("div");
                    oDiv.style.padding = "8px"; oDiv.style.margin = "4px 0"; oDiv.style.borderRadius = "4px";
                    oDiv.innerText = opt.text;

                    if (opt.isCorrect) {
                        oDiv.style.background = "#d4edda"; oDiv.style.color = "#155724";
                        oDiv.innerText += "  ✔ (Correct Answer)";
                    } else if (selectedAnswers[i] === opt.text) {
                        oDiv.style.background = "#f8d7da"; oDiv.style.color = "#721c24";
                        oDiv.innerText += "  ✖ (Your Wrong Choice)";
                    } else {
                        oDiv.style.background = "#fff"; oDiv.style.border = "1px solid #eee";
                    }
                    div.appendChild(oDiv);
                });

                if (q.explanation) {
                    let exp = document.createElement("div");
                    exp.className = "explanation-box"; exp.innerHTML = `<b>Explanation:</b> ${q.explanation}`;
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
        HTML_TEMPLATE, test_title=test_data["title"], test_type=test_data["type"],
        questions_json=json.dumps(test_data["questions"], default=str)
    )
@app.route("/submit-score", methods=["POST"])
def submit_score():
    data = request.json
    if data:
        results_col.insert_one({
            "user_id": data.get("user_id"), "username": data.get("username"),
            "first_name": data.get("first_name"), "test_title": data.get("test_title"),
            "score": data.get("score"), "timestamp": time.time()
        })
        return jsonify({"status": "success"})
    return jsonify({"status": "failed"}), 400

# ==========================================
# 5. NEW MAGIC SYNC ROUTE 
# ==========================================
@app.route("/sync-bot")
def sync_bot():
    """जब आप इस लिंक पर क्लिक करेंगे, तो बॉट बैकग्राउंड में टेस्ट भेजना शुरू कर देगा"""
    threading.Thread(target=generate_and_publish_all).start()
    return "<h2>🚀 Bot Started Successfully! <br><br>बैकग्राउंड में सारे टेस्ट टेलीग्राम पर भेजे जा रहे हैं। कृपया अपना टेलीग्राम ग्रुप चेक करें।</h2>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
