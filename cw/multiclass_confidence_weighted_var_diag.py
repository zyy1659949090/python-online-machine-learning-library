import numpy as np
import logging as logger
import time
from collections import defaultdict
from sklearn.metrics import confusion_matrix
from sklearn.datasets import load_svmlight_file
from base import ConfidenceWeightedModel
from scipy.stats import norm
from scipy import sparse
from scipy.sparse import csr_matrix


class MCWVarDiag(ConfidenceWeightedModel):
    """
    Diagonal elements of matrix version of Confidence-Weighted algorithm;
    non-diagonal elements in covariance matrix are ignored.
    
    References:
    - http://www.aclweb.org/anthology/D/D09/D09-1052.pdf
    - https://alliance.seas.upenn.edu/~nlp/publications/pdf/dredze2008f.pdf
    
    Feature function F(x, y) is chosen as cartesian product of x and y.
    x is feature vector and y is 1-of-K vector.

    This model is applied to multiclass-multilabel classification, solved with
    single constraint update in http://www.aclweb.org/anthology/D/D09/D09-1052.pdf.
    """

    def __init__(self, eta=0.9, epochs=10):
        """
        model initialization.
        """
        logger.basicConfig(level=logger.DEBUG)
        logger.info("init starts")

        super(MCWVarDiag, self).__init__(epochs)
        self._init_model(eta)
        
        logger.info("init finished")

    def _init_model(self, eta):
        """
        Initialize model.
        """
        logger.info("init model starts")
        self.model["mu"] = defaultdict()  # model parameter mean
        self.model["S"] = defaultdict()     # model parameter covariance
        self.model["eta"] = eta                  # confidence parameter
        self.model["phi"] = norm.ppf(norm.cdf(eta))  # inverse of cdf(eta)
        logger.info("init model finished")
        
    def _learn(self, ):
        """
        Learn internally.
        """

    def _update_for_dense_sample(self, sample, y, r):
        """
        Update model parameter internally.
        update rule is as follows,
        mu = mu + alpha * y * Sx
        S = (S^{-1} + 2 * alpha * phi * diag(g_{y, r}^2))^{-1}
        g_{y, r} = F(x, y) - F(x, r)

        Note: diagonal elements are only considered.
        
        Arguments:
        - `sample`: sample, or feature vector
        - `y`: true label
        - `r`: predicted label (!=y) with high rank value
        """

        # components
        phi = self.model["phi"]
        sample = self._add_bias_for_dense_sample(sample)
        g_y = sample
        g_r = -sample
        m = self.model["mu"][y].dot(g_y) + self.model["mu"][r].dot(g_r)
        first_term = (g_y * self.model["S"][y]).dot(g_y)
        second_term = (g_r * self.model["S"][r]).dot(g_r)
        v = first_term + second_term
        a = 1 + 2 * phi * m

        # gamma/alpha
        gamma = (-a + np.sqrt(a * a - 8 * phi * (m - phi * v))) / (4 * phi * v)
        alpha = max(0, gamma)

        # mu
        mu_y = self.model["mu"][y] + alpha * self.model["S"][y] * g_y
        mu_r = self.model["mu"][r] + alpha * self.model["S"][r] * g_r
        self.model["mu"][y] = mu_y
        self.model["mu"][r] = mu_r
        
        # S
        S_y = 1 / (1 / self.model["S"][y] + 2 * alpha * phi * g_y * g_y)
        S_r = 1 / (1 / self.model["S"][r] + 2 * alpha * phi * g_r * g_r)
        self.model["S"][y] = S_y
        self.model["S"][r] = S_r
        
    def _update_for_sparse_sample(self, sample, y, r):
        """
        Update model parameter internally.
        update rule is as follows,
        mu = mu + alpha * y * Sx
        S = (S^{-1} + 2 * alpha * phi * diag(g_{y, r}^2))^{-1}
        g_{y, r} = F(x, y) - F(x, r)

        Note: diagonal elements are only considered.
        
        Arguments:
        - `sample`: sample, or feature vector
        - `y`: true label
        - `r`: predicted label (!=y) with high rank value
        """

        # components
        phi = self.model["phi"]
        sample = self._add_bias_for_sparse_sample(sample)
        g_y = sample
        g_r = -sample
        gg = sample.multiply(sample)
        m = (self.model["mu"][y].multiply(g_y)).sum() + (self.model["mu"][r].multiply(g_r)).sum()
                   
        first_term = (self.model["S"][y].multiply(gg)).sum()
        second_term = (self.model["S"][r].multiply(gg)).sum()
        v = first_term + second_term
        a = 1 + 2 * phi * m

        # gamma/alpha
        gamma = (-a + np.sqrt(a * a - 8 * phi * (m - phi * v))) / (4 * phi * v)
        alpha = max(0, gamma)

        # mu
        Sy_gy = self.model["S"][y].multiply(g_y)
        mu_y = self.model["mu"][y] + Sy_gy.multiply(alpha)
        self.model["mu"][y] = mu_y

        Sr_gr = self.model["S"][r].multiply(g_r)
        mu_r = self.model["mu"][r] + Sr_gr.multiply(alpha)
        self.model["mu"][r] = mu_r
        
        # S
        S_y_inv = self.inverse_1d_sparse_matrix(self.model["S"][y])
        S_y = self.inverse_1d_sparse_matrix(S_y_inv + gg.multiply(2 * alpha * phi))

        S_r_inv = self.inverse_1d_sparse_matrix(self.model["S"][r])
        S_r = self.inverse_1d_sparse_matrix(S_r_inv + gg.multiply(2 * alpha * phi))

        self.model["S"][y] = S_y
        self.model["S"][r] = S_r

        pass

    def learn(self, X, y):
        """
        Learn.
        """
        self.data["sparse"] = sparse.issparse(X)
        if self.data["sparse"]:
            self._learn_for_sparse_samples(X, y)
        else:
            self._learn_for_dense_samples(X, y)
        pass

    def _learn_for_dense_samples(self, X, y):
        """
        Learn for dense samples.
        """
        self.data["n_samples"] = X.shape[0]
        self.data["f_dims"] = X.shape[1]
        self.data["classes"] = np.unique(y)

        logger.info("learn starts")
        for k in self.data["classes"]:
            self.model["mu"][k] = np.zeros(self.data["f_dims"] + 1)
            self.model["S"][k] = np.ones(self.data["f_dims"] + 1)   # only for diagonal
        
        # learn
        st = time.time()
        for e in xrange(0, self.epochs):
            logger.debug("iter: %d" % e)
            for i in xrange(0, self.data["n_samples"]):
                sample = X[i, :]
                label = y[i]
                pred_vals = self._predict_values_for_dense_sample(sample)
                high_rank_class = pred_vals[0][0]
                if high_rank_class != label:  # highest rank class
                    self._update_for_dense_sample(sample, label, high_rank_class)

        logger.info("learn finished")
        et = time.time()
        logger.info("learning time: %f[s]" % (et - st))

    def _learn_for_sparse_samples(self, X, y):
        """
        Learn for sparse samples
        """
        self.data["n_samples"] = X.shape[0]
        self.data["f_dims"] = X.shape[1]
        self.data["classes"] = np.unique(y)

        logger.info("learn starts")
        for k in self.data["classes"]:
            self.model["mu"][k] = csr_matrix(np.zeros(self.data["f_dims"] + 1))
            self.model["S"][k] = csr_matrix(np.ones(self.data["f_dims"] + 1))   # only for diagonal
        
        # learn
        st = time.time()
        for e in xrange(0, self.epochs):
            logger.debug("iter: %d" % e)
            for i in xrange(0, self.data["n_samples"]):
                if i % 1000 == 0:
                    logger.debug("#samples = %d" % i)
                    pass
                sample = X[i, :]
                label = y[i]
                pred_vals = self._predict_values_for_sparse_sample(sample)
                high_rank_class = pred_vals[0][0]
                if high_rank_class != label:  # highest rank class
                    self._update_for_sparse_sample(sample, label, high_rank_class)

        logger.info("learn finished")
        et = time.time()
        logger.info("learning time: %f[s]" % (et - st))

    def _predict_values_for_dense_sample(self, sample):
        """
        predict value of \mu^T * x
        
        Arguments:
        - `sample`:
        """

        values = defaultdict()
        sample = self._add_bias_for_dense_sample(sample)
        for k in self.data["classes"]:
            values[k] = self.model["mu"][k].dot(sample)
    
        # return as list of tuple (class, ranking) in descending order
        return [(k, v) for k, v in sorted(values.items(),
                                          key=lambda x:x[1], reverse=True)]

    def _predict_values_for_sparse_sample(self, sample):
        """
        predict value of \mu^T * x
        
        Arguments:
        - `sample`:
        """

        values = defaultdict()
        sample = self._add_bias_for_sparse_sample(sample)
        for k in self.data["classes"]:
            values[k] = (self.model["mu"][k].multiply(sample)).sum()
    
        # return as list of tuple (class, ranking) in descending order
        return [(k, v) for k, v in sorted(values.items(),
                                          key=lambda x:x[1], reverse=True)]

    def predict(self, sample):
        """
        
        Arguments:
        - `sample`:
        """
        if self.data["sparse"]:
            return self._predict_for_sparse_sample(sample)
        else:
            return self._predict_for_dense_sample(sample)
        
    def _predict_for_dense_sample(self, sample):
        """
        predict class base on argmax_{z} w^T F(x, z)
        
        Arguments:
        - `sample`:
        """
        pred_vals = self._predict_values_for_dense_sample(sample)
        self.cache["pred_vals"] = pred_vals
        return pred_vals[0][0]

    def _predict_for_sparse_sample(self, sample):
        """
        predict class base on argmax_{z} w^T F(x, z)
        
        Arguments:
        - `sample`:
        """
        pred_vals = self._predict_values_for_sparse_sample(sample)
        self.cache["pred_vals"] = pred_vals
        return pred_vals[0][0]
        
    ## TODO
    def update(self, label, sample):
        """
        update model.
        Arguments:
        - `label`: label
        - `sample`: sample, or feature vector
        """
        pass
    pass


def main():
    """
    Example of how to use
    """
    # data load
    #fname = "/home/kzk/datasets/uci_csv/iris.csv"
    fname = "/home/kzk/datasets/uci_csv/glass.csv"
    #fname = "/home/kzk/datasets/uci_csv/breast_cancer.csv"
    #fname = "/home/kzk/datasets/uci_csv/car.csv"
    #fname = "/home/kzk/datasets/uci_csv/credit.csv"
    #fname = "/home/kzk/datasets/uci_csv/usps.csv"
    #fname = "/home/kzk/datasets/uci_csv/liver.csv"
    #fname = "/home/kzk/datasets/uci_csv/haberman.csv"
    #fname = "/home/kzk/datasets/uci_csv/pima.csv"
    #fname = "/home/kzk/datasets/uci_csv/parkinsons.csv"
    #fname = "/home/kzk/datasets/uci_csv/ionosphere.csv"
    #fname = "/home/kzk/datasets/uci_csv/isolet.csv"
    #fname = "/home/kzk/datasets/uci_csv/magicGamaTelescope.csv"
    #fname = "/home/kzk/datasets/uci_csv/mammographic.csv"
    #fname = "/home/kzk/datasets/uci_csv/yeast.csv"
    #fname = "/home/kzk/datasets/news20/news20.dat"
    fname = "/home/k_yoshiyama/datasets/news20/news20.dat"
    print "dataset is", fname
    
    #data = np.loadtxt(fname, delimiter=" ")
    #X = data[:, 1:]
    #y = data[:, 0]

    (X, y) = load_svmlight_file(fname)
    n_samples = X.shape[0]
    y_pred = np.ndarray(n_samples)
    #X = X.toarray()
    
    # learn
    model = MCWVarDiag(eta=0.9, epochs=1)
    model.learn(X, y)

    # predict
    st = time.time()
    for i in xrange(0, n_samples):
        if i % 1000 == 0:
            print "#samples = %d" % i
            pass
        sample = X[i, :]
        y_pred[i] = model.predict(sample)
    et = time.time()
    print "prediction time: %f[s]" % (et - st)
    print "prediction time/sample: %f[s]" % ((et - st) / n_samples)
    
    # show result
    cm = confusion_matrix(y, y_pred)
    #print cm
    print "accurary: %d [%%]" % (np.sum(cm.diagonal()) * 100.0 / np.sum(cm))

if __name__ == '__main__':
    main()
