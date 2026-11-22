"""Shared model pieces for the Strategy Lab (gun): safe to put in front of any scikit-learn model.

KeepSeen (2.82): drops the input columns that are empty (every value missing) in the training rows, and the same columns
when the model scores new rows. scikit-learn 1.9 stopped accepting an all-missing column in gradient-boosted trees
("window shape cannot be larger than input array shape", from its binning); older versions gave such a column no bins
and no splits, so the trees come out the same either way. Columns with any value are kept, untouched (missing values
inside them still go to the trees as missing).
"""
from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin


class KeepSeen(TransformerMixin, BaseEstimator):
    def fit(self, X, y=None):
        X = np.asarray(X, dtype=float)
        self.keep_ = ~np.all(np.isnan(X), axis=0)
        self.n_features_in_ = X.shape[1]
        self.dropped_ = [int(i) for i in np.nonzero(~self.keep_)[0]]
        return self

    def transform(self, X):
        X = np.asarray(X, dtype=float)
        return X[:, self.keep_]
