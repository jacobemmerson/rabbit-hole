Simulate the hybrid recommendation scoring pipeline for a hypothetical user profile. Shows step-by-step how candidates are ranked so you can validate the algorithm before the UI exists.

**Usage:** `/score-sim [inline-cookie-json]`

If no argument is provided, use a default example profile with a mix of rated and unrated articles, varied scroll depths, and a small reading history to exercise both cold-start and warm-user paths.

**Arguments:** $ARGUMENTS

## Steps

1. Parse the cookie JSON from the argument, or use this default if none provided:
   ```json
   {
     "selected_categories": ["Science & Mathematics", "History"],
     "reading_history": [
       { "title": "Quantum_mechanics", "rating": 4, "scroll_depth": 82 },
       { "title": "Photosynthesis", "rating": 2, "scroll_depth": 20 },
       { "title": "Roman_Empire", "rating": null, "scroll_depth": 55 }
     ]
   }
   ```

2. Determine which scoring path applies:
   - **Cold start** (fewer than 3 articles): category graph only
   - **Warm user** (3+ articles): hybrid embedding + category blend

3. Compute article weights from the reading history:
   - Base weight from rating: unrated=1.0, 1★=0.2, 2★=0.5, 3★=1.0, 4★=1.5, 5★=2.0
   - Scroll depth bonus: >75% adds +0.3, <25% subtracts 0.3
   - Show the final weight for each history article

4. Show alpha blending value: `alpha = min(len(reading_history) / 10, 1.0)`

5. If warm user path: describe how the weighted average taste vector would be formed from the history embeddings (no need to actually run the model — explain the math and what the vector represents conceptually).

6. List 5 hypothetical candidate articles and show how they would be ranked by:
   - Category score
   - Embedding similarity score (conceptual)
   - Final blended score: `alpha * embedding + (1 - alpha) * category`

7. Summarize: which article wins and why, and flag any edge cases in the current profile (e.g., all low ratings, missing scroll data).
