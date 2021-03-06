import numpy as np
import Simulators
from Util import *
import scipy.linalg
import Semibandits


class LinearContext():
    def __init__(self,name,features):
        self.features = features
        self.name = name
    def get_ld_features(self):
        return self.features
    def get_K(self):
        return self.features.shape[0]
    def get_L(self):
        return 1
    def get_ld_dim(self):
        return self.features.shape[1]
    def get_name(self):
        return self.name

def truncate_context(context, d):
    return LinearContext(context.get_name(), context.get_ld_features()[:,0:d])
        

class LimeCB(Semibandits.Semibandit):
    class PassthroughSimulator(Simulators.LinearBandit):
        def __init__(self,B, d):
            self.B = B
            self.d = d
            self.K = self.B.K
            self.L = self.B.L
        

    def __init__(self,B):
        self.B = B
        self.override = None

    def init_linucb(self):
        self.base_learner = Semibandits.LinUCB(self.passthrough)
        self.base_learner.init(self.T,params={'delta': self.delta})
        
    def init_minimonster(self):
        learning_alg = lambda: sklearn.linear_model.LinearRegression()
        self.base_learner = Semibandits.MiniMonster(self.passthrough,learning_alg=learning_alg,classification=False)
        self.base_learner.init(self.T,params={'mu':self.mu})
        self.base_learner.verbose = False

    def init(self, T, params={}):

        self.random_state = np.random.RandomState(params['seed'])

        self.T = T
        self.max_d = self.B.d
        self.dimensions = [2**i for i in range(1, int(np.log2(self.max_d)))]
        self.dimensions.append(self.max_d)
        
        if self.override is not None:
            self.dimensions = [self.override]
    
        self.d = self.dimensions[0]
    
        self.b_vec = np.matrix(np.zeros((self.d,1)))
        self.cov = np.matrix(np.eye(self.d))
        self.Cinv = scipy.linalg.inv(self.cov)
        self.weights = self.Cinv*self.b_vec
        self.t = 1

        self.global_b = np.matrix(np.zeros((self.max_d,1)))
        self.global_cov = np.matrix(np.zeros((self.max_d,self.max_d)))
        self.random_samples = 0

        if "delta" in params.keys():
            self.delta = params['delta']
        else:
            self.delta = 0.05
        if "schedule" in params.keys():
            self.schedule = params['schedule']
        else:
            self.schedule = 100
        if 'mu' in params.keys():
            self.mu = params['mu']
        else:
            self.mu = 0
            
        self.passthrough = LimeCB.PassthroughSimulator(self.B, self.d)
        self.init_learner = None
        self.learner_type = params['base']
        if params['base'] == 'linucb':
            self.init_learner = self.init_linucb
        if params['base'] == 'minimonster':
            self.init_learner = self.init_minimonster
        self.init_learner()

        self.random = False
        self.reward = []
        self.opt_reward = []

    def _get_mu(self):
        """
        Return the current value of mu_t
        """
        ## a = 1.0/(2*self.B.K)
        ## b = np.sqrt(np.log(16.0*(self.t**2)*self.B.N/self.delta)/float(self.B.K*self.B.L*self.t))
        a = self.mu
        b = self.mu*np.sqrt(self.B.K)/np.sqrt(self.B.L*self.t)
        c = np.min([a,b])
        return np.min([1,c])

    def update(self, x, A, y_vec, r):
        """
        Update the regression target and feature cov. 
        """
        if not self.random:
            self.base_learner.update(truncate_context(x,self.d), A, y_vec, r)

        features = np.matrix(x.get_ld_features())
        for i in range(features.shape[0]):
            self.global_cov += features[i,:].T*features[i,:]
        if self.random or (self.learner_type == 'minimonster' and self.base_learner.num_unif > self.base_num_unif):
            for i in range(len(A)):
                self.global_b += y_vec[i]*features[A[i],:].T
                self.random_samples += 1

        self.t += 1
        if self.t % 10 == 0:
            self.estimate_residual()

    def get_action(self, x):
        """
        Find the UCBs for the predicted reward for each base action
        and play the composite action that maximizes the UCBs
        subject to whatever constraints. 
        """
        ber = self.random_state.binomial(1, self._get_mu())
        if ber == 1:
            self.random = True
            K = x.get_K()
            act = self.random_state.choice(K)
            return [act]
        else:
            self.random = False
            if self.learner_type == 'minimonster':
                self.base_num_unif = self.base_learner.num_unif
            return self.base_learner.get_action(truncate_context(x, self.d))

    def estimate_residual(self):
        to_move = None
        done = False
        for d in self.dimensions:
            if d <= self.d or d >= 16*self.d or done:
                continue
            ### Construct R matrix
            tmp = np.matrix(np.zeros((d,d)))
            tmp[0:self.d,0:self.d] = self.global_cov[0:self.d,0:self.d]
            Sigma = self.global_cov[0:d,0:d]
            try:
                R = scipy.linalg.pinv2(tmp) - scipy.linalg.pinv2(Sigma)
            except np.linalg.linalg.LinAlgError:
                continue
            score = self.global_b[0:d].T*R*Sigma*R*self.global_b[0:d]/self.random_samples**2
#             print(score)
#            print("[LimeCB] curr_d=%d, test_d=%d, score=%0.3f, thres=%0.2f" % (self.d, d, score, np.sqrt(d)/self.random_samples), flush=True)
            if score > 0.01*np.sqrt(d)/self.random_samples and self.random_samples > 5*d:
                ### Then we switch!
                print("[LimeCB] Switching to d=%d" % (d), flush=True)
                self.d = d
                self.passthrough = LimeCB.PassthroughSimulator(self.B, self.d)
                self.init_learner()
                done = True
            
if __name__=='__main__':
    import sys, os, argparse, time
    import sklearn.linear_model

    parser = argparse.ArgumentParser()
    parser.add_argument('--T', action='store', default=50000, help='number of rounds', type=int)
    parser.add_argument('--iters', action='store', default=1, type=int)
    parser.add_argument('--d', action='store', default=20, type=int)
    parser.add_argument('--s', action='store', default=20, type=int)
    parser.add_argument('--K', action='store', default=5, type=int)

    parser.add_argument('--alg', action='store', default='all', choices=['linucb','limecb','oracle'])
    parser.add_argument('--base', action='store', default='minimonster', choices=['minimonster', 'linucb'])
    parser.add_argument('--param', action='store', default=None)
    parser.add_argument('--noise', action='store', default=None)
    parser.add_argument('--loss', action='store', default=False)
                        

    Args = parser.parse_args(sys.argv[1:])
    print(Args,flush=True)
    if Args.noise is not None:
        Args.noise = float(Args.noise)

    outdir = './results/T=%d_d=%d_s=%d_K=%d_sig=%0.1f/' % (Args.T, Args.d, Args.s, Args.K, Args.noise)
    if not os.path.isdir(outdir):
        os.mkdir(outdir)

    if Args.param is not None:
        Args.param = float(Args.param)

    rewards = []
    regrets = []
    for i in range(Args.iters):
        S = Simulators.LinearBandit(Args.d, 1, Args.K, noise=Args.noise, seed=i, pos=False, low=Args.s)
        if Args.alg == 'linucb':
            Alg = Semibandits.LinUCB(S)
            if Args.param is not None:
                start = time.time()
                (r,reg,val_tmp) = Alg.play(Args.T, verbose=True, params={'delta': Args.param, 'schedule': 10})
                stop = time.time()
        if Args.alg == 'limecb':
            Alg = LimeCB(S)
            if Args.param is not None:
                start = time.time()
                (r,reg,val_tmp) = Alg.play(Args.T, verbose=True, params={'mu': Args.param, 'schedule': 10, 'seed': i, 'base': Args.base})
                stop = time.time()
        if Args.alg == 'oracle' and Args.base == 'minimonster':
            Alg = LimeCB(S)
            Alg.override = Args.s
            if Args.param is not None:
                start = time.time()
                (r,reg,val_tmp) = Alg.play(Args.T, verbose=True, params={'mu': Args.param, 'schedule': 10, 'seed': i, 'base': Args.base})
                stop=time.time()
        if Args.alg == 'oracle' and Args.base == 'linucb':
            print("here")
            Alg = LimeCB(S)
            Alg.override = Args.s
            if Args.param is not None:
                start = time.time()
                (r,reg,val_tmp) = Alg.play(Args.T, verbose=True, params={'delta': Args.param, 'schedule': 10, 'seed': i, 'base': Args.base})
                stop=time.time()
        rewards.append(r)
        regrets.append(reg)

    np.savetxt(outdir+"%s_%s_%0.5f_rewards.out" % (Args.alg, Args.base, Args.param), rewards)
    np.savetxt(outdir+"%s_%s_%0.5f_regrets.out" % (Args.alg, Args.base, Args.param), regrets)
    print("---- DONE ----")
