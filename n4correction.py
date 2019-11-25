from __future__ import division
import os, sys, glob
from multiprocessing import Pool, cpu_count

import ants
from ants import ANTsImage


def n4_correction(im_input):
    command = 'N4BiasFieldCorrection -d 3 -i ' + im_input + ' ' + ' -s 3 -c [50x50x30x20] -b [300] -o ' + im_input.replace('.nii.gz', '_corrected.nii.gz')
    os.system(command)


def n4_correction_ants(im_input):
    image = ants.image_read(im_input, 3)
    image_target = im_input.replace('.nii.gz', '_corrected.nii.gz')

    image_result: ANTsImage = ants.n4_bias_field_correction(image,
                                                            shrink_factor=3,
                                                            convergence={'iters': [50, 50, 30, 20], 'tol': 1e-07},
                                                            spline_param=300,
                                                            verbose=False)

    image_result.to_file(image_target)
    print(f"Saved image: ${image_target}")


def batch_works(k):
    if k == n_processes - 1:
        paths = all_paths[k * int(len(all_paths) / n_processes):]
    else:
        paths = all_paths[k * int(len(all_paths) / n_processes): (k + 1) * int(len(all_paths) / n_processes)]
        
    for path in paths:
        n4_correction_ants(glob.glob(os.path.join(path, '*_t1.nii.gz'))[0])
        n4_correction_ants(glob.glob(os.path.join(path, '*_t1ce.nii.gz'))[0])
        n4_correction_ants(glob.glob(os.path.join(path, '*_t2.nii.gz'))[0])
        n4_correction_ants(glob.glob(os.path.join(path, '*_flair.nii.gz'))[0])


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise Exception("Need at least the input data directory")
    input_path = sys.argv[1]
    prefix = sys.argv[3] if sys.argv == 3 else 'BraTS19'
        
    all_paths = []
    for dirpath, dirnames, files in os.walk(input_path):
        if os.path.basename(dirpath)[0:7] == prefix:
            all_paths.append(dirpath)
            
    n_processes = cpu_count()
    pool = Pool(processes=n_processes)
    pool.map(batch_works, range(n_processes))