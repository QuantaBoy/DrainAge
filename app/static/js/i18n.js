// Tamil and Hindi for the dashboard and the shared top bar. The page is written in
// English; this swaps its words in place as they appear, including text the scripts
// write later (a MutationObserver), and puts the English back when switched off.
// ponytail: an exact-text dictionary plus patterns for text with numbers in it. Long
// explanatory paragraphs stay in English; add them here to translate them.

(() => {
    const LANGS = { en: "English", ta: "தமிழ்", hi: "हिन्दी" };

    // English -> [Tamil, Hindi]
    const WORDS = {
        "Flood Nowcast": ["வெள்ள முன்னறிவிப்பு", "बाढ़ पूर्वानुमान"],
        "Dashboard": ["டாஷ்போர்டு", "डैशबोर्ड"],
        "Resources": ["ஆதாரங்கள்", "संसाधन"],
        "Technology": ["தொழில்நுட்பம்", "तकनीक"],
        "streets wet": ["தெருக்களில் நீர்", "सड़कों पर पानी"],
        "impassable": ["கடக்க இயலாதவை", "रास्ता बंद"],
        "manholes": ["மேன்ஹோல்கள்", "मैनहोल"],
        "Guide": ["வழிகாட்டி", "गाइड"],
        "Overview": ["மேலோட்டம்", "अवलोकन"],
        "Route": ["வழி", "रास्ता"],
        "Alerts": ["எச்சரிக்கைகள்", "चेतावनी"],
        "What-if": ["ஒருவேளை", "अगर ऐसा हो"],
        "Streets": ["தெருக்கள்", "सड़कें"],
        "← Back": ["← பின்செல்", "← वापस"],
        "Chennai street-level flood nowcast, 0 to 3 hours ahead":
            ["சென்னை தெரு வாரியான வெள்ள முன்னறிவிப்பு, அடுத்த 0–3 மணி நேரம்", "चेन्नई सड़क-स्तरीय बाढ़ पूर्वानुमान, अगले 0–3 घंटे"],

        // Alerts
        "See what a 60 mm/h storm would do": ["60 மிமீ/மணி மழை பெய்தால் என்ன ஆகும் எனப் பாருங்கள்", "60 मिमी/घंटा बारिश में क्या होगा, देखें"],
        "Back to the live forecast": ["நேரடி முன்னறிவிப்புக்குத் திரும்பு", "लाइव पूर्वानुमान पर लौटें"],
        "New here? Take the 1-minute tour": ["புதியவரா? 1 நிமிட அறிமுகம் பாருங்கள்", "नए हैं? 1 मिनट का परिचय देखें"],
        "Going under in the next 30 minutes": ["அடுத்த 30 நிமிடங்களில் நீரில் மூழ்குபவை", "अगले 30 मिनट में डूबने वाली"],
        "No named street goes under in the next 30 minutes.": ["அடுத்த 30 நிமிடங்களில் எந்தத் தெருவும் நீரில் மூழ்காது.", "अगले 30 मिनट में कोई सड़क नहीं डूबेगी।"],
        "No named street is under water at this time.": ["இந்த நேரத்தில் எந்தத் தெருவும் நீரில் இல்லை.", "इस समय कोई सड़क पानी में नहीं है।"],

        // Streets table
        "Streets · next 3 hours": ["தெருக்கள் · அடுத்த 3 மணி நேரம்", "सड़कें · अगले 3 घंटे"],
        "Goes under soonest": ["முதலில் மூழ்குபவை", "सबसे पहले डूबने वाली"],
        "Deepest": ["அதிக ஆழம்", "सबसे गहरी"],
        "Deepest right now": ["இப்போது அதிக ஆழம்", "अभी सबसे गहरी"],
        "Download CSV": ["CSV பதிவிறக்கு", "CSV डाउनलोड करें"],
        "Street": ["தெரு", "सड़क"],
        "Locality · ward": ["பகுதி · வார்டு", "इलाका · वार्ड"],
        "Goes under": ["மூழ்கும் நேரம்", "डूबने का समय"],
        "Impassable from": ["கடக்க இயலாத நேரம்", "रास्ता बंद होने का समय"],
        "Peak (cm)": ["உச்ச ஆழம் (செமீ)", "अधिकतम (सेमी)"],
        "Peak at": ["உச்ச நேரம்", "अधिकतम समय"],
        "Clears": ["வடியும் நேரம்", "पानी उतरेगा"],
        "At slider (cm)": ["தேர்ந்த நேரத்தில் (செமீ)", "चुने समय पर (सेमी)"],
        "Filter by street, locality or ward": ["தெரு, பகுதி அல்லது வார்டு மூலம் தேடு", "सड़क, इलाका या वार्ड से खोजें"],

        // Route
        "Flood-safe route": ["வெள்ளமில்லா பாதுகாப்பான வழி", "बाढ़-सुरक्षित रास्ता"],
        "Type a place or pick it on the map. The route goes round water over 30 cm and avoids shallower water where a dry road is not much longer.":
            ["இடத்தைத் தட்டச்சு செய்யுங்கள் அல்லது வரைபடத்தில் தேர்ந்தெடுங்கள். 30 செமீக்கு மேல் நீர் உள்ள சாலைகளைத் தவிர்த்து, உலர்ந்த சாலை சிறிது நீளமாக இருந்தாலும் அதையே வழி தேர்ந்தெடுக்கும்.",
             "जगह लिखें या नक्शे पर चुनें। रास्ता 30 सेमी से गहरे पानी से बचता है, और जहाँ सूखी सड़क थोड़ी ही लंबी हो वहाँ कम पानी से भी बचता है।"],
        "From": ["புறப்படும் இடம்", "कहाँ से"],
        "To": ["சேருமிடம்", "कहाँ तक"],
        "Map": ["வரைபடம்", "नक्शा"],
        "Use my selected location as start": ["தேர்ந்த இடத்தைத் தொடக்கமாகக் கொள்", "चुनी जगह से शुरू करें"],
        "Leave": ["புறப்படும் நேரம்", "निकलने का समय"],
        "Now": ["இப்போது", "अभी"],
        "In 30 min": ["30 நிமிடத்தில்", "30 मिनट में"],
        "In 1 hour": ["1 மணி நேரத்தில்", "1 घंटे में"],
        "In 1 h 30": ["1½ மணி நேரத்தில்", "1½ घंटे में"],
        "In 2 hours": ["2 மணி நேரத்தில்", "2 घंटे में"],
        "In 2 h 30": ["2½ மணி நேரத்தில்", "2½ घंटे में"],
        "Search": ["தேடல் முறை", "खोज विधि"],
        "A* (guided by straight-line distance)": ["A* (நேர்கோட்டுத் தூரம் வழிகாட்டும்)", "A* (सीधी दूरी से निर्देशित)"],
        "Dijkstra (explores outward evenly)": ["Dijkstra (எல்லாத் திசையிலும் சமமாகத் தேடும்)", "Dijkstra (सभी दिशाओं में बराबर खोज)"],
        "Find safe route": ["பாதுகாப்பான வழியைக் காண்", "सुरक्षित रास्ता खोजें"],
        "Clear": ["அழி", "साफ़ करें"],
        "Safe route": ["பாதுகாப்பான வழி", "सुरक्षित रास्ता"],
        "Start: your location, or a place": ["தொடக்கம்: உங்கள் இடம் அல்லது ஒரு இடம்", "शुरुआत: आपकी जगह या कोई स्थान"],
        "Where to? e.g. Guindy": ["எங்கே? எ.கா. கிண்டி", "कहाँ जाना है? जैसे गिंडी"],
        "e.g. T Nagar, or pick on the map": ["எ.கா. தி. நகர், அல்லது வரைபடத்தில் தேர்வு", "जैसे टी. नगर, या नक्शे पर चुनें"],
        "e.g. Velachery, Chennai Central": ["எ.கா. வேளச்சேரி, சென்னை சென்ட்ரல்", "जैसे वेलाचेरी, चेन्नई सेंट्रल"],

        // What-if
        "What-if storm": ["ஒருவேளை புயல் மழை பெய்தால்", "अगर तेज़ बारिश हो"],
        "Try a storm and a drain condition, and see which streets go under.":
            ["ஒரு மழை அளவையும் வடிகால் நிலையையும் தேர்ந்தெடுத்து, எந்தத் தெருக்கள் மூழ்கும் எனப் பாருங்கள்.", "बारिश और नाली की हालत चुनें, और देखें कौन-सी सड़कें डूबेंगी।"],
        "Rainfall": ["மழை அளவு", "बारिश"],
        "Live forecast (Open-Meteo)": ["நேரடி முன்னறிவிப்பு (Open-Meteo)", "लाइव पूर्वानुमान (Open-Meteo)"],
        "Drains working at": ["வடிகால் செயல்திறன்", "नालियों की क्षमता"],
        "Full surveyed capacity (best case)": ["முழு திறன் (சிறந்த நிலை)", "पूरी क्षमता (सबसे अच्छी स्थिति)"],
        "Half of it (silted)": ["பாதி (வண்டல் படிந்தது)", "आधी (गाद भरी)"],
        "A quarter of it (badly silted)": ["கால் பங்கு (அதிக வண்டல்)", "चौथाई (बहुत गाद भरी)"],
        "Nothing (blocked)": ["இல்லை (அடைபட்டது)", "शून्य (बंद)"],
        "Over the whole 3 hours": ["முழு 3 மணி நேரத்தில்", "पूरे 3 घंटों में"],
        "Streets that go under": ["மூழ்கும் தெருக்கள்", "डूबने वाली सड़कें"],
        "Of them impassable (30 cm+)": ["அவற்றில் கடக்க இயலாதவை (30 செமீ+)", "इनमें से बंद (30 सेमी+)"],
        "Backed by the flood record": ["கடந்த வெள்ளப் பதிவுடன் பொருந்துபவை", "पिछली बाढ़ के रिकॉर्ड से मेल"],
        "Road under water": ["நீரில் உள்ள சாலை", "पानी में सड़क"],
        "Manholes surcharging": ["நிரம்பி வழியும் மேன்ஹோல்கள்", "उफनते मैनहोल"],
        "Rain in total": ["மொத்த மழை", "कुल बारिश"],

        // Overview
        "Weather & rainfall": ["வானிலை & மழை", "मौसम और बारिश"],
        "Use my location": ["என் இருப்பிடம்", "मेरी जगह"],
        "Go": ["காண்", "देखें"],
        "Weather for a district": ["மாவட்ட வானிலை", "ज़िले का मौसम"],
        "Rain movement, next 24 h": ["மழை நகர்வு, அடுத்த 24 மணி", "बारिश की चाल, अगले 24 घंटे"],
        "▶ Play": ["▶ இயக்கு", "▶ चलाएँ"],
        "Light": ["லேசான", "हल्की"],
        "Moderate": ["மிதமான", "मध्यम"],
        "Heavy": ["கனமான", "भारी"],
        "Violent": ["மிகக் கனமான", "अति भारी"],
        "Storm water drains": ["மழைநீர் வடிகால்கள்", "बरसाती नालियाँ"],
        "Storm Water Drains": ["மழைநீர் வடிகால்கள்", "बरसाती नालियाँ"],
        "Show the drain network on the map": ["வடிகால் வலையமைப்பை வரைபடத்தில் காட்டு", "नालियों का नेटवर्क नक्शे पर दिखाएँ"],
        "Zone": ["மண்டலம்", "ज़ोन"],
        "All Zones": ["அனைத்து மண்டலங்கள்", "सभी ज़ोन"],
        "Ward": ["வார்டு", "वार्ड"],
        "All Wards": ["அனைத்து வார்டுகள்", "सभी वार्ड"],
        "Drain Type": ["வடிகால் வகை", "नाली का प्रकार"],
        "All Types": ["அனைத்து வகைகள்", "सभी प्रकार"],
        "Closed": ["மூடிய", "बंद"],
        "Open Drain": ["திறந்த வடிகால்", "खुली नाली"],
        "Side Drain": ["பக்க வடிகால்", "किनारे की नाली"],
        "Side drain": ["பக்க வடிகால்", "किनारे की नाली"],
        "Status": ["நிலை", "स्थिति"],
        "All Statuses": ["அனைத்து நிலைகள்", "सभी स्थितियाँ"],
        "Bad": ["மோசம்", "खराब"],
        "Good": ["நன்று", "अच्छी"],
        "Yes": ["ஆம்", "हाँ"],
        "Colour by": ["நிறம் எதன்படி", "रंग किस आधार पर"],
        "Live load (rain vs capacity)": ["நேரடிச் சுமை (மழை / திறன்)", "लाइव भार (बारिश बनाम क्षमता)"],
        "Water flow (invert levels)": ["நீரோட்டத் திசை", "पानी के बहाव की दिशा"],
        "Condition": ["நிலைமை", "हालत"],
        "Under half full": ["பாதிக்கும் குறைவு", "आधे से कम भरी"],
        "Filling (50–85%)": ["நிரம்புகிறது (50–85%)", "भर रही (50–85%)"],
        "At capacity (85–100%)": ["முழுத் திறனில் (85–100%)", "पूरी भरी (85–100%)"],
        "Over capacity: spills to street": ["திறனுக்கு மேல்: தெருவில் வழிகிறது", "क्षमता से अधिक: सड़क पर बहता"],
        "No surveyed size": ["அளவு பதிவில்லை", "आकार दर्ज नहीं"],
        "Loading drains…": ["வடிகால்கள் ஏற்றப்படுகின்றன…", "नालियाँ लोड हो रही हैं…"],
        "About the model": ["மாதிரி பற்றி", "मॉडल के बारे में"],
        "Data sources & ward maps →": ["தரவு மூலங்கள் & வார்டு வரைபடங்கள் →", "डेटा स्रोत और वार्ड नक्शे →"],

        // Map and timebar
        "Layers": ["அடுக்குகள்", "परतें"],
        "Flood forecast, 0–3 h (depth in cm)": ["வெள்ள முன்னறிவிப்பு, 0–3 மணி (ஆழம் செமீ)", "बाढ़ पूर्वानुमान, 0–3 घंटे (गहराई सेमी)"],
        "Elevation (terrain height)": ["நில உயரம்", "ज़मीन की ऊँचाई"],
        "Streets (Chennai)": ["தெருக்கள் (சென்னை)", "सड़कें (चेन्नई)"],
        "Rain": ["மழை", "बारिश"],
        "Temperature": ["வெப்பநிலை", "तापमान"],
        "Wind": ["காற்று", "हवा"],
        "Clouds": ["மேகங்கள்", "बादल"],
        "Water depth": ["நீர் ஆழம்", "पानी की गहराई"],
        "Running the live flood forecast": ["நேரடி வெள்ள முன்னறிவிப்பு கணக்கிடப்படுகிறது", "लाइव बाढ़ पूर्वानुमान चल रहा है"],
        "Rainfall, next 3 hours": ["மழை, அடுத்த 3 மணி நேரம்", "बारिश, अगले 3 घंटे"],
        "Terrain and buildings": ["நிலப்பரப்பு & கட்டடங்கள்", "ज़मीन और इमारतें"],
        "19,500 drains and manholes": ["19,500 வடிகால்கள் & மேன்ஹோல்கள்", "19,500 नालियाँ और मैनहोल"],
        "Street by street depths": ["தெருவாரியான ஆழம்", "हर सड़क की गहराई"],
        "Named streets under water": ["நீரில் உள்ள தெருக்கள்", "पानी में डूबी सड़कें"],
        "Streets too deep to drive (over 30 cm)": ["வாகனம் செல்ல இயலாத தெருக்கள் (30 செமீக்கு மேல்)", "गाड़ी चलाने लायक नहीं (30 सेमी से अधिक)"],
        "Manholes overflowing": ["நிரம்பி வழியும் மேன்ஹோல்கள்", "उफनते मैनहोल"],
        "Search a street or area": ["தெரு அல்லது பகுதியைத் தேடு", "सड़क या इलाका खोजें"],
        "Play the forecast": ["முன்னறிவிப்பை இயக்கு", "पूर्वानुमान चलाएँ"],
        "Forecast unavailable": ["முன்னறிவிப்பு கிடைக்கவில்லை", "पूर्वानुमान उपलब्ध नहीं"],
        "Live": ["நேரலை", "लाइव"],
        "Language": ["மொழி", "भाषा"],
    };

    // Text with numbers or names in it: [pattern, Tamil, Hindi], $1… are the matches.
    const PATTERNS = [
        [/^LIVE · from (\d\d:\d\d)$/, "நேரலை · $1 முதல்", "लाइव · $1 से"],
        [/^Under water at (.+)$/, "$1 அன்று நீரில் உள்ளவை", "$1 पर पानी में"],
        [/^Rain ([\d.]+) mm\/h$/, "மழை $1 மிமீ/மணி", "बारिश $1 मिमी/घंटा"],
        [/^([\d.]+) mm\/h$/, "$1 மிமீ/மணி", "$1 मिमी/घंटा"],
        [/^([\d.]+) mm$/, "$1 மிமீ", "$1 मिमी"],
        [/^([\d.,]+) km$/, "$1 கிமீ", "$1 किमी"],
        [/^No street flooding expected in the next 3 hours$/, "அடுத்த 3 மணி நேரத்தில் எந்தத் தெருவிலும் வெள்ளம் இல்லை", "अगले 3 घंटों में किसी सड़क पर बाढ़ नहीं"],
        [/^No street flooding expected in this storm$/, "இந்த மழையில் எந்தத் தெருவிலும் வெள்ளம் இல்லை", "इस बारिश में किसी सड़क पर बाढ़ नहीं"],
        [/^([\d,]+) streets? flood in the next 3 hours$/, "அடுத்த 3 மணி நேரத்தில் $1 தெருக்களில் வெள்ளம்", "अगले 3 घंटों में $1 सड़कों पर बाढ़"],
        [/^([\d,]+) streets? flood in this storm$/, "இந்த மழையில் $1 தெருக்களில் வெள்ளம்", "इस बारिश में $1 सड़कों पर बाढ़"],
        [/^([\d.]+) mm of rain forecast\. The forecast updates every 30 minutes\.$/,
            "$1 மிமீ மழை எதிர்பார்க்கப்படுகிறது. முன்னறிவிப்பு ஒவ்வொரு 30 நிமிடத்திற்கும் புதுப்பிக்கப்படும்.",
            "$1 मिमी बारिश का अनुमान। पूर्वानुमान हर 30 मिनट में अपडेट होता है।"],
        [/^([\d.]+) mm of rain in total\. The forecast updates every 30 minutes\.$/,
            "மொத்தம் $1 மிமீ மழை. முன்னறிவிப்பு ஒவ்வொரு 30 நிமிடத்திற்கும் புதுப்பிக்கப்படும்.",
            "कुल $1 मिमी बारिश। पूर्वानुमान हर 30 मिनट में अपडेट होता है।"],
        [/^([\d,]+) under water at (.+?)\. (?:([\d,]+) get too deep to drive through\. )?Deepest: (.+), (\d+) cm\.$/,
            (m) => `${m[2]} அன்று ${m[1]} தெருக்கள் நீரில். ${m[3] ? `${m[3]} வாகனம் செல்ல இயலாத ஆழம். ` : ""}அதிக ஆழம்: ${m[4]}, ${m[5]} செமீ.`,
            (m) => `${m[2]} पर ${m[1]} सड़कें पानी में। ${m[3] ? `${m[3]} गाड़ी के लिए बहुत गहरी। ` : ""}सबसे गहरी: ${m[4]}, ${m[5]} सेमी।`],
        [/^in (\d+) min$/, "$1 நிமிடத்தில்", "$1 मिनट में"],
        [/^Scenario: (\d+) mm\/h for 3 h \((\d+) mm total\)$/, "சூழல்: 3 மணி நேரம் $1 மிமீ/மணி (மொத்தம் $2 மிமீ)", "परिदृश्य: 3 घंटे $1 मिमी/घंटा (कुल $2 मिमी)"],
        [/^SCENARIO: (\d+) mm\/h for 3 h\. Hypothetical rain, not a forecast\.$/, "சூழல்: 3 மணி நேரம் $1 மிமீ/மணி. கற்பனை மழை, முன்னறிவிப்பு அல்ல.", "परिदृश्य: 3 घंटे $1 मिमी/घंटा। काल्पनिक बारिश, पूर्वानुमान नहीं।"],
        [/^Running a (\d+) mm\/h storm$/, "$1 மிமீ/மணி மழை கணக்கிடப்படுகிறது", "$1 मिमी/घंटा बारिश की गणना हो रही है"],
        [/^Streets · (\d+) mm\/h what-if$/, "தெருக்கள் · $1 மிமீ/மணி சூழல்", "सड़कें · $1 मिमी/घंटा परिदृश्य"],
        [/^Show more \((\d+) left\)$/, "மேலும் காட்டு ($1 மீதம்)", "और दिखाएँ ($1 बाकी)"],
        [/^Ward (\d+)$/, "வார்டு $1", "वार्ड $1"],
        [/^Ward (\d+) · Zone ([IVX]+)$/, "வார்டு $1 · மண்டலம் $2", "वार्ड $1 · ज़ोन $2"],
        [/^Zone ([IVX]+) – (.+)$/, "மண்டலம் $1 – $2", "ज़ोन $1 – $2"],
        [/^\+(\d) h$/, "+$1 மணி", "+$1 घंटा"],
    ];

    const col = { ta: 0, hi: 1 };
    let lang = "en";
    const english = new WeakMap();              // node -> its English text
    const ATTRS = ["placeholder", "aria-label", "title"];

    function translate(text) {
        const key = text.replace(/\s+/g, " ").trim();
        if (!key) return null;
        const hit = WORDS[key];
        if (hit) return text.replace(key, hit[col[lang]]);
        for (const [re, ...out] of PATTERNS) {
            const m = key.match(re);
            if (m) {
                const t = out[col[lang]];
                return text.replace(key, typeof t === "function" ? t(m) : key.replace(re, t));
            }
        }
        return null;
    }

    function textNode(node) {
        const known = english.get(node);
        // Text a script wrote since the last pass is the new English.
        if (!known || node.nodeValue !== known.shown) english.set(node, { en: node.nodeValue, shown: null });
        const record = english.get(node);
        const shown = lang === "en" ? record.en : (translate(record.en) ?? record.en);
        record.shown = shown;
        if (node.nodeValue !== shown) node.nodeValue = shown;
    }

    function element(el) {
        for (const name of ATTRS) {
            const store = `i18n${name.replace("-", "")}`;
            const current = el.getAttribute(name);
            if (current == null) continue;
            if (el.dataset[store + "Shown"] !== current) el.dataset[store] = current;
            const shown = lang === "en" ? el.dataset[store] : (translate(el.dataset[store]) ?? el.dataset[store]);
            el.dataset[store + "Shown"] = shown;
            if (current !== shown) el.setAttribute(name, shown);
        }
    }

    function walk(root) {
        if (root.nodeType === Node.TEXT_NODE) return textNode(root);
        if (root.nodeType !== Node.ELEMENT_NODE || root.closest?.("script,style,.leaflet-tile-pane")) return;
        element(root);
        const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
        while (w.nextNode()) {
            const n = w.currentNode;
            if (n.nodeType === Node.TEXT_NODE) {
                if (!n.parentElement.closest("script,style")) textNode(n);
            } else element(n);
        }
    }

    const observer = new MutationObserver((changes) => {
        observer.disconnect();
        for (const c of changes) {
            if (c.type === "childList") c.addedNodes.forEach(walk);
            else if (c.type === "characterData") textNode(c.target);
            else if (c.type === "attributes") element(c.target);
        }
        watch();
    });
    const watch = () => observer.observe(document.body, {
        childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS,
    });

    function setLang(next) {
        lang = LANGS[next] ? next : "en";
        document.documentElement.lang = lang;
        try { localStorage.setItem("lang", lang); } catch (err) {}
        observer.disconnect();
        walk(document.body);
        watch();
        const select = document.getElementById("lang");
        if (select) select.value = lang;
    }

    function start() {
        const select = document.getElementById("lang");
        select?.addEventListener("change", () => setLang(select.value));
        let saved = "en";
        try { saved = localStorage.getItem("lang") || "en"; } catch (err) {}
        setLang(saved);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();
