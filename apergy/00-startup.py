import ipynbname
import apergy as Apergy
from IPython import get_ipython
ip = get_ipython()
global apergy
apergy = Apergy.Apergy(ip=ip)
ip.events.register('pre_run_cell', apergy._pre_run_cell)

# Default Lib Imports
import matplotlib 
import numpy as np
