def compute_score(layer_scores, weights):
    total_weight = sum(weights.values())
    score = sum(layer_scores.get(layer, 50.0) * (weights[layer] / total_weight) for layer in weights)
    return score