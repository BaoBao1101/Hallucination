Title: From Speculative LLM Ideas to Testable MVPs:
Controlled Hallucination Distillation for Venture
Ideation

Sẽ chứng minh trên 2 kiểu prompt (Prompt 0, Prompt 1)
Hiện đã hiện thực và output prompt 0 (folder final)
Prompt 0 (Creativity Prompt Direction)
Domain: 
+ Coffee Services
+ Web App Services
+ Game Mobile
+ Restaurant Services
+ Offline 2D Games
+ EdTech
+ Artificial Intelligence App
+ Online Business
+ Youtube and Tiktok

Mỗi domain 4 ideas
Ablation:
Baseline:
- M0 method: Direct Low-temperature mode (0.0)
+ Output files: ideas_raw_direct_*.json

- M1 methods: Direct high-temperature mode: (2.0):
+ Output files: ideas_raw_*.json

-CHVD method (M3): Pipeline Applied Hallucination Method (2.0 -> 0.0):
+ Output files: ideas_filtered_*.json

Code chuẩn hóa các file output json về 1 file, sau đó xào trộn và ẩn danh (ID): 01_shuffle_merge_blind_dataset.py (final/01_shuffle_merge_blind_dataset)
-> Chuẩn hóa về 1 file, xào trộn các ideas và ẩn danh ideas (dùng để LLM judges) sau đó chia ra các batch (folder blind_eval_10domains_b12)
File mapping Blind ID -> M0-M3 Methods blind_key_private.csv
-> Chấm metrics individual cho từng method output (ChatGPT, Gemini2.5, Gemini 3.1 folders), không gửi file mapping cho LLM để cho LLM không biết và chấm ngẫu nhiên mới công bằng
Metrics: 
        "novelty":
        "conceptual_coherence":
        "user_pain_plausibility":
        "technical_plausibility":
        "mvp_clarity":
        "business_model_plausibility":
        "unsupported_claim_risk":
        "ethical_privacy_risk":
        "overall_mvp_usefulness":
        "prototype_willingness": 

Dùng toán thống kê để thống kê kết quả tổng quát cho output này
Đưa 4-5 batch ngẫu nhiên cho 3-4 người chấm dựa trên thang này rồi thống kê lại giúp t dùng để làm human evaluation: (1-5)
Mày có thể test metrics cho từng domain (mục tiêu cụ thể là xem với từng domain ideas, M0 M1 M3 có chỉ số như thế nào)


File mapping M1-M3 pairwise_key_private.csv (Dùng để chứng minh M3 vẫn còn giữ tinh hoa hallucination của M1)
-> Chấm metrics paired cho từng method output (ChatGPT, Gemini2.5, Gemini 3.1 folders), không gửi file mapping cho LLM để cho LLM không biết và chấm ngẫu nhiên mới công bằng
File để gửi chấm: pairwise_distillation_input.jsonl

Metrics:
"creative_distinctiveness":
"conceptual_coherence":
"technical_plausibility":
"mvp_clarity":
"business_model_plausibility":
"unsupported_claim_risk":
"ethical_privacy_risk":
